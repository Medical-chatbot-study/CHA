import math
import os
import sqlite3
from collections import Counter, defaultdict
from typing import ClassVar

from openCHA.tasks.task import BaseTask


class HivDrugChooseTask(BaseTask):
    name: str = "hiv_drug_choose"
    chat_name: str = "HivDrugChoose"
    description: str = "Riceve un profilo HIV in una sola stringa nel formato HIV-RNA=valore|CD4=valore|CD4/CD8=valore. Cerca nel database i pazienti HIV piu simili e restituisce i farmaci osservati nei casi vicini con misure di similarita e supporto. Il task NON sceglie il farmaco: la scelta finale deve essere effettuata dall'LLM usando questi dati come supporto alla decisione."
    dependencies: list[str] = []
    inputs: list[str] = ["Una sola stringa nel formato HIV-RNA=valore|CD4=valore|CD4/CD8=valore. Esempio: HIV-RNA=248|CD4=277|CD4/CD8=0.16. Passare sempre l'intero profilo come una singola stringa."]
    outputs: list[str] = ["Contesto con pazienti HIV simili, score di similarita, farmaci osservati, frequenze e supporto pesato. L'LLM usa il contesto per scegliere e motivare il farmaco piu appropriato; il task non effettua automaticamente la scelta."]
    output_type: bool = False
    return_direct: bool = False

    db_path: ClassVar[str] = os.path.abspath(os.path.join("data", "hiv_anonimizzato.sqlite"))
    top_k: ClassVar[int] = 20
    max_profiles_for_drug_lookup: ClassVar[int] = 200
    context_cases: ClassVar[int] = 10
    context_drugs: ClassVar[int] = 8
    similarity_alpha: ClassVar[float] = 6.0

    def _parse_profile(self, inputs):
        if len(inputs) != 1:
            raise ValueError("Expected one string in the format HIV-RNA=valore|CD4=valore|CD4/CD8=valore.")

        parts = [part.strip() for part in str(inputs[0]).split("|")]

        if len(parts) != 3:
            raise ValueError("Expected HIV-RNA=valore|CD4=valore|CD4/CD8=valore.")

        values = {}

        aliases = {
            "HIV-RNA": "hiv_rna",
            "HIV RNA": "hiv_rna",
            "HIV_RNA": "hiv_rna",
            "CD4": "cd4",
            "CD4/CD8": "cd4_cd8",
            "CD4-CD8": "cd4_cd8",
            "CD4_CD8": "cd4_cd8",
        }

        for part in parts:
            if "=" not in part:
                raise ValueError("Each exam must contain a name and a value separated by '='.")

            raw_name, raw_value = part.split("=", 1)
            normalized_name = raw_name.strip().upper()
            metric = aliases.get(normalized_name)

            if metric is None:
                raise ValueError(f"Unknown exam name: {raw_name.strip()}.")

            if metric in values:
                raise ValueError(f"Duplicate exam: {raw_name.strip()}.")

            values[metric] = raw_value.strip()

        required = {"hiv_rna", "cd4", "cd4_cd8"}

        if set(values) != required:
            raise ValueError("Expected exactly HIV-RNA, CD4 and CD4/CD8.")

        try:
            hiv_rna = float(values["hiv_rna"].replace(",", "."))
            cd4 = float(values["cd4"].replace(",", "."))
            cd4_cd8 = float(values["cd4_cd8"].replace(",", "."))
        except ValueError as error:
            raise ValueError("HIV-RNA, CD4 and CD4/CD8 must contain numeric values.") from error

        if hiv_rna <= 0 or cd4 <= 0 or cd4_cd8 <= 0:
            raise ValueError("HIV-RNA, CD4 and CD4/CD8 must be greater than zero.")

        return {
            "hiv_rna": hiv_rna,
            "cd4": cd4,
            "cd4_cd8": cd4_cd8,
        }

    def _ranges(self, targets):
        return {
            "hiv_min": max(1.0, targets["hiv_rna"] / 4.0),
            "hiv_max": targets["hiv_rna"] * 4.0,
            "cd4_min": max(1.0, targets["cd4"] - 200.0),
            "cd4_max": targets["cd4"] + 200.0,
            "ratio_min": max(0.01, targets["cd4_cd8"] - 0.10),
            "ratio_max": targets["cd4_cd8"] + 0.10,
        }

    def _get_candidate_exams(self, conn, ranges):
        number_sql = "CAST(REPLACE(TRIM(e.valore), ',', '.') AS REAL)"

        query = f"SELECT e.n_cartella_galileo AS patient_id, {number_sql} AS numeric_value, 'hiv_rna' AS metric FROM esami e JOIN anamnesi a ON a.n_cartella_galileo = e.n_cartella_galileo WHERE a.tipologia = 'HIV+' AND LOWER(e.descrizione) LIKE '%hiv%rna%' AND LOWER(e.descrizione) NOT LIKE '%liquor%' AND TRIM(e.valore) GLOB '[0-9]*' AND {number_sql} BETWEEN ? AND ? UNION ALL SELECT e.n_cartella_galileo AS patient_id, {number_sql} AS numeric_value, 'cd4' AS metric FROM esami e JOIN anamnesi a ON a.n_cartella_galileo = e.n_cartella_galileo WHERE a.tipologia = 'HIV+' AND LOWER(TRIM(e.descrizione)) IN ('cd4+', 'cd4+ (n)', 'cd4 (nr assoluto)', 'cd4 (nr assoluto) - s') AND TRIM(e.valore) GLOB '[0-9]*' AND {number_sql} BETWEEN ? AND ? UNION ALL SELECT e.n_cartella_galileo AS patient_id, {number_sql} AS numeric_value, 'cd4_cd8' AS metric FROM esami e JOIN anamnesi a ON a.n_cartella_galileo = e.n_cartella_galileo WHERE a.tipologia = 'HIV+' AND LOWER(TRIM(e.descrizione)) IN ('cd4/cd8', 'cd4+/cd8+', 'rapporto cd4/cd8', 'rapporto cd4/cd8 - s') AND TRIM(e.valore) GLOB '[0-9]*' AND {number_sql} BETWEEN ? AND ?"

        params = (
            ranges["hiv_min"],
            ranges["hiv_max"],
            ranges["cd4_min"],
            ranges["cd4_max"],
            ranges["ratio_min"],
            ranges["ratio_max"],
        )

        return conn.execute(query, params).fetchall()

    def _build_profiles(self, rows, targets):
        grouped = defaultdict(lambda: {
            "hiv_rna": [],
            "cd4": [],
            "cd4_cd8": [],
        })

        for row in rows:
            value = float(row["numeric_value"])

            if value > 0:
                grouped[row["patient_id"]][row["metric"]].append(value)

        profiles = []

        for patient_id, metrics in grouped.items():
            if not metrics["hiv_rna"] or not metrics["cd4"] or not metrics["cd4_cd8"]:
                continue

            hiv_rna = min(
                metrics["hiv_rna"],
                key=lambda value: abs(math.log10(value) - math.log10(targets["hiv_rna"])),
            )

            cd4 = min(
                metrics["cd4"],
                key=lambda value: abs(value - targets["cd4"]),
            )

            cd4_cd8 = min(
                metrics["cd4_cd8"],
                key=lambda value: abs(value - targets["cd4_cd8"]),
            )

            hiv_component = abs(math.log10(hiv_rna) - math.log10(targets["hiv_rna"])) / math.log10(4.0)
            cd4_component = abs(cd4 - targets["cd4"]) / 200.0
            ratio_component = abs(cd4_cd8 - targets["cd4_cd8"]) / 0.10

            score = math.sqrt(
                (
                    hiv_component ** 2
                    + cd4_component ** 2
                    + ratio_component ** 2
                )
                / 3.0
            )

            profiles.append({
                "patient_id": patient_id,
                "hiv_rna": hiv_rna,
                "cd4": cd4,
                "cd4_cd8": cd4_cd8,
                "score": score,
            })

        profiles.sort(
            key=lambda item: (
                item["score"],
                item["patient_id"],
            )
        )

        return profiles

    def _get_latest_drugs(self, conn, profiles):
        patient_ids = [
            profile["patient_id"]
            for profile in profiles[:self.max_profiles_for_drug_lookup]
        ]

        if not patient_ids:
            return {}

        placeholders = ",".join("?" for _ in patient_ids)

        query = f"WITH ranked_switches AS (SELECT s.n_cartella_galileo AS patient_id, s.switch_number, s.data_switch, s.classe, ROW_NUMBER() OVER (PARTITION BY s.n_cartella_galileo ORDER BY CAST(s.data_switch AS INTEGER) DESC, s.switch_number DESC) AS rn FROM switch s WHERE s.n_cartella_galileo IN ({placeholders})) SELECT r.patient_id, r.data_switch, r.classe, sm.farmaco FROM ranked_switches r JOIN somministrazione sm ON sm.n_cartella_galileo = r.patient_id AND sm.switch_number = r.switch_number WHERE r.rn = 1 ORDER BY r.patient_id"

        rows = conn.execute(query, patient_ids).fetchall()

        drugs = {}

        for row in rows:
            if row["farmaco"] is None:
                continue

            drug = str(row["farmaco"]).strip()

            if not drug:
                continue

            drugs[row["patient_id"]] = {
                "drug": drug,
                "drug_class": str(row["classe"]).strip() if row["classe"] is not None else "N/A",
                "switch_year": row["data_switch"],
            }

        return drugs

    def _link_top_patients(self, profiles, drugs):
        linked = []

        for profile in profiles:
            drug = drugs.get(profile["patient_id"])

            if drug is None:
                continue

            linked.append({
                **profile,
                **drug,
            })

            if len(linked) >= self.top_k:
                break

        return linked

    def _summarize_drug_evidence(self, linked):
        frequency = Counter()
        weighted_support = defaultdict(float)
        score_sum = defaultdict(float)
        best_score = {}

        for item in linked:
            drug = item["drug"]

            weight = math.exp(
                -self.similarity_alpha * item["score"]
            )

            frequency[drug] += 1
            weighted_support[drug] += weight
            score_sum[drug] += item["score"]

            best_score[drug] = min(
                best_score.get(drug, float("inf")),
                item["score"],
            )

        total_weight = sum(
            weighted_support.values()
        )

        drugs = sorted(
            frequency,
            key=lambda drug: (
                -weighted_support[drug],
                -frequency[drug],
                score_sum[drug] / frequency[drug],
                best_score[drug],
                drug,
            ),
        )

        return [
            {
                "drug": drug,
                "frequency": frequency[drug],
                "weighted_share": weighted_support[drug] / total_weight if total_weight else 0.0,
                "mean_score": score_sum[drug] / frequency[drug],
                "best_score": best_score[drug],
            }
            for drug in drugs
        ]

    def _make_context(self, targets, ranges, linked, evidence):
        lines = [
            "CONTESTO DAL DATABASE HIV",
            f"Profilo richiesto: HIV-RNA={targets['hiv_rna']}, CD4={targets['cd4']}, CD4/CD8={targets['cd4_cd8']}",
            f"Range di ricerca: HIV-RNA={ranges['hiv_min']:.0f}-{ranges['hiv_max']:.0f}, CD4={ranges['cd4_min']:.0f}-{ranges['cd4_max']:.0f}, CD4/CD8={ranges['ratio_min']:.2f}-{ranges['ratio_max']:.2f}",
            f"Pazienti simili con farmaco disponibile: {len(linked)}",
        ]

        if not linked:
            lines.append(
                "Nessun paziente simile con farmaco disponibile e stato trovato."
            )
            lines.append(
                "ISTRUZIONE LLM: spiegare che il database non fornisce evidenza sufficiente per supportare la scelta di un farmaco."
            )
            return "\n".join(lines)

        lines.append(
            "EVIDENZA SUI FARMACI NEI PAZIENTI SIMILI:"
        )

        for item in evidence[:self.context_drugs]:
            lines.append(
                f"- farmaco={item['drug']}; pazienti={item['frequency']}/{len(linked)}; supporto_pesato={item['weighted_share']:.1%}; miglior_score={item['best_score']:.4f}; score_medio={item['mean_score']:.4f}"
            )

        lines.append(
            "CASI PIU SIMILI:"
        )

        for item in linked[:self.context_cases]:
            lines.append(
                f"- score={item['score']:.4f}; HIV-RNA={item['hiv_rna']}; CD4={item['cd4']}; CD4/CD8={item['cd4_cd8']}; farmaco={item['drug']}; classe={item['drug_class']}; anno={item['switch_year']}"
            )

        lines.append(
            "ISTRUZIONE LLM: selezionare un solo farmaco come supporto alla decisione usando l'intero contesto. Considerare soprattutto la similarita dei singoli pazienti, la coerenza dei casi piu vicini, la frequenza e il supporto pesato dei farmaci. Non scegliere automaticamente il farmaco piu frequente: frequenza e supporto sono evidenze, non una regola di decisione. Usare anche il ragionamento clinico del modello per confrontare le alternative. Rispondere con FARMACO e MOTIVO in modo breve. Non inventare risultati clinici, efficacia osservata o informazioni sul paziente non presenti nel contesto."
        )

        return "\n".join(lines)

    def _execute(self, inputs):
        try:
            targets = self._parse_profile(inputs)
        except ValueError as error:
            return f"Invalid input: {error}"

        if not os.path.exists(self.db_path):
            return f"Database not found: {self.db_path}"

        ranges = self._ranges(targets)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            rows = self._get_candidate_exams(
                conn,
                ranges,
            )

            profiles = self._build_profiles(
                rows,
                targets,
            )

            drugs = self._get_latest_drugs(
                conn,
                profiles,
            )

            linked = self._link_top_patients(
                profiles,
                drugs,
            )

            evidence = self._summarize_drug_evidence(
                linked,
            )

            return self._make_context(
                targets,
                ranges,
                linked,
                evidence,
            )

        except sqlite3.Error as error:
            return f"Database error in hiv_drug_choose: {error}"

        finally:
            conn.close()
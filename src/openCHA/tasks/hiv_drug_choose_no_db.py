from openCHA.tasks.task import BaseTask


class HivDrugChooseNoDbTask(BaseTask):
    name: str = "hiv_drug_choose_without_db"
    chat_name: str = "HivDrugChooseWithoutDb"
    description: str = "Riceve un profilo HIV in una sola stringa nel formato HIV-RNA=valore|CD4=valore|CD4/CD8=valore. Non interroga alcun database e non utilizza pazienti simili. Restituisce all'LLM esclusivamente il profilo clinico fornito e le istruzioni necessarie per scegliere un singolo farmaco o regime antiretrovirale sulla base del ragionamento clinico generale, esplicitando i limiti dovuti alle informazioni disponibili."
    dependencies: list[str] = []
    inputs: list[str] = ["Una stringa nel formato HIV-RNA=valore|CD4=valore|CD4/CD8=valore. Esempio: HIV-RNA=248|CD4=277|CD4/CD8=0.16."]
    outputs: list[str] = ["Contesto con il profilo HIV richiesto e istruzioni per scegliere un singolo farmaco o regime antiretrovirale senza utilizzare dati provenienti dal database o pazienti simili. La risposta deve motivare brevemente la scelta e dichiarare i principali limiti dell'inferenza."]
    output_type: bool = False
    return_direct: bool = False

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

    def _make_context(self, targets):
        lines = [
            "PROFILO HIV",
            f"HIV-RNA={targets['hiv_rna']}",
            f"CD4={targets['cd4']}",
            f"CD4/CD8={targets['cd4_cd8']}",
            "FONTE DEI DATI: il task non utilizza alcun database, non ricerca pazienti simili e non dispone di farmaci osservati in casi precedenti. La scelta deve quindi basarsi esclusivamente sui valori forniti e su conoscenze cliniche generali consolidate.",
            "ISTRUZIONE LLM: scegliere un solo farmaco o regime antiretrovirale come opzione clinicamente plausibile e spiegare brevemente il motivo. Interpretare HIV-RNA come principale indicatore dell'attivita virologica e della risposta virologica nel corretto contesto temporale, CD4 come indicatore dello stato immunologico e del recupero immunitario, e CD4/CD8 come informazione aggiuntiva sul profilo immunologico che non deve essere usata da sola per determinare uno specifico trattamento. Non affermare che i tre valori disponibili siano sufficienti a stabilire con certezza la terapia ottimale e non inventare resistenze, precedenti trattamenti, aderenza, comorbidita, coinfezioni, interazioni farmacologiche, funzione renale o epatica o altri dati non presenti. Se la scelta dipende da informazioni mancanti, indicarlo esplicitamente. Rispondere in modo conciso con le sezioni FARMACO, MOTIVAZIONE e LIMITI.",
        ]

        return "\n".join(lines)

    def _execute(self, inputs):
        try:
            targets = self._parse_profile(inputs)
        except ValueError as error:
            return f"Invalid input: {error}"

        return self._make_context(targets)
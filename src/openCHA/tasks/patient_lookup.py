import os
import sqlite3

from openCHA.tasks.task import BaseTask


# Questo task costruisce un riepilogo clinico sintetico di un paziente
# partendo da n_cartella_galileo e interrogando il database SQLite.
class PatientLookupTask(BaseTask):
  name: str = "patient_lookup"
  chat_name: str = "PatientLookup"
  description: str = "Retrieve an HIV-relevant clinical summary for a patient using n_cartella_galileo passed as a string."
  dependencies: list[str] = []
  inputs: list[str] = ["The patient's n_cartella_galileo as a string containing only digits, for example '11869'."]
  outputs: list[str] = ["A structured textual clinical summary containing demographics, anamnesis, recent exams, therapy switches, and administered drugs if available."]
  output_type: bool = False
  return_direct: bool = False

  db_path: str = os.path.abspath(os.path.join("data", "hiv_anonimizzato.sqlite"))

  # Normalizza gli input ricevuti dal task convertendo ogni valore in stringa
  # e rimuovendo eventuali spazi superflui all'inizio e alla fine.
  def _parse_input(self, input_args):
    return [str(arg).strip() for arg in input_args]

  # Restituisce un valore sicuro da stampare nel riepilogo finale.
  # Se il dato è nullo o vuoto, usa "N/A" per evitare output sporchi o ambigui.
  def _safe(self, value):
    if value is None:
      return "N/A"

    if isinstance(value, str) and not value.strip():
      return "N/A"

    return str(value)

  # Recupera i dati anagrafici minimi del paziente necessari alla sezione iniziale
  # del riepilogo clinico.
  def _get_patient(self, conn, n_cartella_galileo):
    return conn.execute(
      """
      SELECT n_cartella_galileo, sesso, etnia
      FROM pazienti
      WHERE n_cartella_galileo = ?
      """,
      (n_cartella_galileo,)
    ).fetchone()

  # Recupera l'anamnesi HIV del paziente. Se non esiste alcun record,
  # il metodo restituisce None e la sezione finale verrà gestita come assente.
  def _get_anamnesi(self, conn, n_cartella_galileo):
    return conn.execute(
      """
      SELECT tipologia, epidemiologia, data_hiv_positivo, data_hiv_negativo, nadir_tipo, nadir_data, cdc, data_ultima_visita
      FROM anamnesi
      WHERE n_cartella_galileo = ?
      """,
      (n_cartella_galileo,)
    ).fetchone()

  # Recupera gli switch terapeutici più recenti e, per ciascuno, aggiunge
  # i farmaci somministrati in modo da avere una struttura completa per il rendering.
  def _get_switches(self, conn, n_cartella_galileo, limit=5):
    switches = conn.execute(
      """
      SELECT switch_number, data_switch, classe, note
      FROM switch
      WHERE n_cartella_galileo = ?
      ORDER BY data_switch DESC, switch_number DESC
      LIMIT ?
      """,
      (n_cartella_galileo, limit)
    ).fetchall()

    # Questo blocco arricchisce ogni switch con i farmaci associati.
    # La lista finale contiene dizionari già pronti per essere trasformati in testo.
    enriched_switches = []

    for switch_row in switches:
      drugs = conn.execute(
        """
        SELECT s.farmaco, s.dosaggio, f.note AS farmaco_note
        FROM somministrazione s
        LEFT JOIN farmaci f ON f.farmaco = s.farmaco
        WHERE s.n_cartella_galileo = ? AND s.switch_number = ?
        ORDER BY s.farmaco
        """,
        (n_cartella_galileo, switch_row["switch_number"])
      ).fetchall()

      enriched_switches.append({
        "switch_number": switch_row["switch_number"],
        "data_switch": switch_row["data_switch"],
        "classe": switch_row["classe"],
        "note": switch_row["note"],
        "drugs": drugs
      })

    return enriched_switches

  # Recupera gli esami clinici più recenti del paziente, ordinati dal più nuovo
  # al più vecchio, così da mostrare prima le informazioni più rilevanti.
  def _get_recent_exams(self, conn, n_cartella_galileo, limit=20):
    return conn.execute(
      """
      SELECT data_esame, descrizione, valore, esito, um, ref_altro, codifica, sottocategoria, cromatico
      FROM esami
      WHERE n_cartella_galileo = ?
      ORDER BY data_esame DESC, esame_numero ASC
      LIMIT ?
      """,
      (n_cartella_galileo, limit)
    ).fetchall()

  # Esegue il flusso completo del task:
  # valida l'input, legge i dati dal database e costruisce il testo finale.
  def _execute(self, inputs):
    n_cartella_galileo_raw = inputs[0].strip()

    # Questo blocco valida subito il formato dell'identificativo.
    # In questo modo vengono evitate query inutili con input non numerici.
    try:
      n_cartella_galileo = int(n_cartella_galileo_raw)
    except ValueError:
      return "Invalid input: n_cartella_galileo must contain only digits."

    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row

    try:
      patient = self._get_patient(conn, n_cartella_galileo)

      if patient is None:
        return f"No patient found with n_cartella_galileo={n_cartella_galileo}."

      anamnesi = self._get_anamnesi(conn, n_cartella_galileo)
      switches = self._get_switches(conn, n_cartella_galileo, limit=5)
      recent_exams = self._get_recent_exams(conn, n_cartella_galileo, limit=20)
    finally:
      conn.close()

    # Questo blocco costruisce il riepilogo finale in sezioni stabili.
    # La lista di righe semplifica la formattazione e la manutenzione futura.
    lines = []

    lines.append(f"Patient summary for n_cartella_galileo={patient['n_cartella_galileo']}")
    lines.append("")
    lines.append("Demographics:")
    lines.append(f"- Sex: {self._safe(patient['sesso'])}")
    lines.append(f"- Ethnicity: {self._safe(patient['etnia'])}")

    lines.append("")
    lines.append("HIV-relevant anamnesis:")

    if anamnesi is None:
      lines.append("- No anamnesis record available.")
    else:
      lines.append(f"- Typology: {self._safe(anamnesi['tipologia'])}")
      lines.append(f"- Epidemiology: {self._safe(anamnesi['epidemiologia'])}")
      lines.append(f"- HIV positive date: {self._safe(anamnesi['data_hiv_positivo'])}")
      lines.append(f"- HIV negative date: {self._safe(anamnesi['data_hiv_negativo'])}")
      lines.append(f"- Nadir type: {self._safe(anamnesi['nadir_tipo'])}")
      lines.append(f"- Nadir date: {self._safe(anamnesi['nadir_data'])}")
      lines.append(f"- CDC stage: {self._safe(anamnesi['cdc'])}")
      lines.append(f"- Last visit date: {self._safe(anamnesi['data_ultima_visita'])}")

    lines.append("")
    lines.append("Recent therapy switches:")

    if not switches:
      lines.append("- No therapy switch records available.")
    else:
      for switch_data in switches:
        lines.append(f"- Switch #{self._safe(switch_data['switch_number'])}: date={self._safe(switch_data['data_switch'])}, class={self._safe(switch_data['classe'])}, note={self._safe(switch_data['note'])}")

        if not switch_data["drugs"]:
          lines.append("  Drugs: none recorded.")
        else:
          drug_parts = []

          for drug in switch_data["drugs"]:
            drug_parts.append(f"{self._safe(drug['farmaco'])} ({self._safe(drug['dosaggio'])})")

          lines.append(f"  Drugs: {', '.join(drug_parts)}")

    lines.append("")
    lines.append("Recent exams:")

    if not recent_exams:
      lines.append("- No exam records available.")
    else:
      for exam in recent_exams:
        lines.append(f"- {self._safe(exam['data_esame'])}: {self._safe(exam['descrizione'])}; value={self._safe(exam['valore'])}; result={self._safe(exam['esito'])}; unit={self._safe(exam['um'])}; reference={self._safe(exam['ref_altro'])}; code={self._safe(exam['codifica'])}; category={self._safe(exam['sottocategoria'])}; color={self._safe(exam['cromatico'])}")

    return "\\n".join(lines)
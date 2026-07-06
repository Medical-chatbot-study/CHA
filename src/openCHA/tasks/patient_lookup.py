import os
import sqlite3

from openCHA.tasks.task import BaseTask


class PatientLookupTask(BaseTask):
  name: str = "patient_lookup"
  chat_name: str = "PatientLookup"
  description: str = "Retrieve basic patient information from the SQLite clinical database using n_cartella_galileo passed as a string."
  dependencies: list[str] = []
  inputs: list[str] = ["The patient's n_cartella_galileo as a string containing only digits, for example '11869'."]
  outputs: list[str] = ["A textual summary containing patient id, sex, and ethnicity if found."]
  output_type: bool = False
  return_direct: bool = False

  db_path: str = os.path.abspath(os.path.join("data", "hiv_anonimizzato.sqlite"))

  def _parse_input(self, input_args):
    return [str(arg).strip() for arg in input_args]

  def _execute(self, inputs):
    n_cartella_galileo_raw = inputs[0].strip()

    try:
      n_cartella_galileo = int(n_cartella_galileo_raw)
    except ValueError:
      return "Invalid input: n_cartella_galileo must contain only digits."

    conn = sqlite3.connect(self.db_path)
    conn.row_factory = sqlite3.Row

    try:
      row = conn.execute(
        """
        SELECT n_cartella_galileo, sesso, etnia
        FROM pazienti
        WHERE n_cartella_galileo = ?
        """,
        (n_cartella_galileo,)
      ).fetchone()
    finally:
      conn.close()

    if row is None:
      return f"No patient found with n_cartella_galileo={n_cartella_galileo}."

    return f"Patient found: n_cartella_galileo={row['n_cartella_galileo']}, sesso={row['sesso']}, etnia={row['etnia']}."
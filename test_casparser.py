import casparser
import traceback

file_path = "DEC2025_AA50698385_TXN.pdf"
password = "NXMPS1822N"

print("Trying casparser with PyMuPDF...")
try:
    data = casparser.read_cas_pdf(file_path, password)
    print("Success!")
    print(f"Statement Period: {data['statement_period']}")
    print(f"File Type: {data['file_type']}")
    print(f"Number of folios: {len(data['folios'])}")
except Exception:
    traceback.print_exc()
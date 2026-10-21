import csv
import io


def export_csv(data, filename="export.csv") -> str:
    if not data:
        raise ValueError("no data to export")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
    writer.writeheader()
    for row in data:
        writer.writerow({k: "" if v is None else v for k, v in row.items()})
    return buf.getvalue()

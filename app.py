from flask import Flask, request, jsonify
import boto3
from datetime import datetime, timedelta
import re
from colorama import init, Fore, Style
import os

# Initialize colorama and Flask
init(autoreset=True)
app = Flask(__name__)

@app.route("/process", methods=["POST"])
def process_image():
    # Use uploaded file if present
    if 'image' in request.files:
        image_bytes = request.files['image'].read()
        document = {"Bytes": image_bytes}
    else:
        document = {"S3Object": {"Bucket": "delamyth1", "Name": "delamythrealdeal.jpg"}}

    # Call Textract
    textract = boto3.client("textract", region_name="eu-west-1")
    response = textract.analyze_document(
        Document=document,
        FeatureTypes=["TABLES", "FORMS"]
    )

    # Extract month and year from any date pattern
    month_year = None
    for block in response.get("Blocks", []):
        text = block.get("Text", "")
        match = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2})", text)
        if match:
            day, month_num, year_suffix = match.groups()
            months = ["January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]
            mi = int(month_num)
            if 1 <= mi <= 12:
                month_year = f"{months[mi-1]} 20{year_suffix}"
                break

    def correct_time_format(text):
        subs = {
            '!': '1', 'I': '1', 'l': '1', '|': '1',
            'O': '0', 'o': '0',
            '%': ':', ';': ':', ',': ':', '.': ':'
        }
        for wrong, right in subs.items():
            text = text.replace(wrong, right)
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 4:
            return f"{digits[:2]}:{digits[2:4]}"
        if len(digits) == 3:
            return f"0{digits[0]}:{digits[1:3]}"
        return text

    cells = {}
    for block in response.get("Blocks", []):
        if block.get("BlockType") == "CELL":
            r, c = block["RowIndex"], block["ColumnIndex"]
            txt = ''
            if "Relationships" in block:
                for rel in block["Relationships"]:
                    if rel["Type"] == "CHILD":
                        for cid in rel["Ids"]:
                            child = next((b for b in response["Blocks"] if b["Id"] == cid), {})
                            if child.get("BlockType") == "WORD":
                                txt += child.get("Text", '') + ' '
            cells.setdefault(r, {})[c] = txt.strip()

    weekly_totals = {}
    daily_hours = {}

    for row_idx, cols in cells.items():
        name_raw = cols.get(1, '').strip()
        name = re.sub(r'\bIN\b', '', name_raw).strip()
        if not name or name.upper() in ("DATE", "DAY", "IN", "OUT"):
            continue

        total_seconds = 0
        daily_seconds = {}

        for c in sorted(cols.keys()):
            if c == 1:
                continue
            entry = cols[c]
            entry = re.sub(r'IN(?=\d)', 'IN ', entry)
            entry = re.sub(r'(?<=\d)OUT', ' OUT', entry)
            parts = re.split(r'\s+', entry)
            times = []
            for part in parts:
                part = correct_time_format(part)
                if re.match(r"^\d{1,2}:\d{2}$", part):
                    times.append(part)

            day_seconds = 0
            for i in range(0, len(times)-1, 2):
                try:
                    start = datetime.strptime(times[i], "%H:%M")
                    end = datetime.strptime(times[i+1], "%H:%M")
                    if end <= start:
                        end += timedelta(hours=12)
                    diff = (end - start).total_seconds()
                    day_seconds += diff
                except ValueError:
                    continue

            daily_seconds[c] = day_seconds
            total_seconds += day_seconds

        weekly_totals[name] = round((total_seconds / 3600) * 4) / 4
        daily_hours[name] = {day: round((sec / 3600) * 4) / 4 for day, sec in daily_seconds.items()}

    nonzero = sorted([n for n, h in weekly_totals.items() if h > 0])
    zero = sorted([n for n, h in weekly_totals.items() if h == 0])
    sorted_names = nonzero + zero

    max_hours = max(weekly_totals.values()) if weekly_totals else 0
    top_performers = [n for n, h in weekly_totals.items() if h == max_hours and h > 0]

    summary = {
        "month": month_year or "Unknown",
        "top_performers": top_performers,
        "weekly_totals": weekly_totals,
        "daily_hours": daily_hours,
    }

    return jsonify(summary)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

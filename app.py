from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import boto3
from datetime import datetime, timedelta
import re
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

@app.route("/", methods=["GET"])
def index():
    # You might want to create a simple HTML template
    return """
    <html>
        <head>
            <title>Timesheet Analyzer</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
                h1 { color: #333; }
                .container { margin-top: 30px; }
                .form-group { margin-bottom: 15px; }
                .btn { background-color: #4CAF50; color: white; padding: 10px 15px; border: none; cursor: pointer; }
                #results { margin-top: 20px; white-space: pre-wrap; }
            </style>
        </head>
        <body>
            <h1>Timesheet Image Analyzer</h1>
            <div class="container">
                <form id="upload-form" enctype="multipart/form-data">
                    <div class="form-group">
                        <label for="image">Upload timesheet image:</label>
                        <input type="file" id="image" name="image" accept="image/*">
                    </div>
                    <button type="submit" class="btn">Process Image</button>
                </form>
                <div id="results"></div>
            </div>
            
            <script>
                document.getElementById('upload-form').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    const resultsDiv = document.getElementById('results');
                    
                    resultsDiv.textContent = 'Processing, please wait...';
                    
                    try {
                        const response = await fetch('/process', {
                            method: 'POST',
                            body: formData
                        });
                        
                        const data = await response.json();
                        
                        if (data.error) {
                            resultsDiv.innerHTML = `<p style="color: red;">Error: ${data.error}</p>`;
                            return;
                        }
                        
                        let html = `<h2>Results for ${data.month}</h2>`;
                        
                        if (data.top_performers.length > 0) {
                            html += `<p><strong>Top performers:</strong> ${data.top_performers.join(', ')} ⭐</p>`;
                        }
                        
                        html += '<h3>Weekly Totals</h3><ul>';
                        
                        // Sort names by hours (descending)
                        const sortedNames = Object.keys(data.weekly_totals).sort((a, b) => 
                            data.weekly_totals[b] - data.weekly_totals[a]
                        );
                        
                        for (const name of sortedNames) {
                            const hours = data.weekly_totals[name];
                            html += `<li>${name}: ${hours.toFixed(2)} hours</li>`;
                        }
                        
                        html += '</ul>';
                        resultsDiv.innerHTML = html;
                    } catch (err) {
                        resultsDiv.innerHTML = `<p style="color: red;">Error: ${err.message}</p>`;
                    }
                });
            </script>
        </body>
    </html>
    """

@app.route("/process", methods=["POST"])
def process_image():
    try:
        # Check if credentials are configured
        aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
        
        if not aws_access_key or not aws_secret_key:
            logger.error("AWS credentials not configured")
            return jsonify({"error": "AWS credentials not configured. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."}), 500
        
        # Process image file if uploaded
        if 'image' in request.files and request.files['image'].filename:
            image_file = request.files['image']
            logger.info(f"Processing uploaded file: {image_file.filename}")
            
            # Validate file type if needed
            if not image_file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif')):
                return jsonify({"error": "Invalid file format. Please upload an image file."}), 400
                
            image_bytes = image_file.read()
            document = {"Bytes": image_bytes}
        else:
            logger.info("No image uploaded, using default S3 image")
            document = {"S3Object": {"Bucket": "delamyth1", "Name": "delamythrealdeal.jpg"}}

        # AWS Textract client
        textract = boto3.client(
            "textract",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name="eu-west-1"
        )

        logger.info("Calling Textract analyze_document")
        response = textract.analyze_document(
            Document=document,
            FeatureTypes=["TABLES", "FORMS"]
        )
        logger.info("Textract analyze_document completed")

        # Extract month and year from any date pattern
        month_year = None
        for block in response.get("Blocks", []):
            text = block.get("Text", "")
            if not text:
                continue
                
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

        # Parse table cells into row/column dict
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

        # Compute weekly totals and daily breakdown
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

        # Find top performer(s)
        max_hours = max(weekly_totals.values()) if weekly_totals else 0
        top_performers = [n for n, h in weekly_totals.items() if h == max_hours and h > 0]

        summary = {
            "month": month_year or "Unknown",
            "top_performers": top_performers,
            "weekly_totals": weekly_totals,
            "daily_hours": daily_hours,
        }

        return jsonify(summary)

    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

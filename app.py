from flask import Flask, request, jsonify
import boto3
import os

app = Flask(__name__)

textract = boto3.client(
    'textract',
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
    region_name='us-east-1'
)

@app.route('/process', methods=['POST'])
def process_image():
    if 'image' not in request.files:
        return jsonify({'error': 'No image uploaded'}), 400

    file = request.files['image']
    image_bytes = file.read()

    response = textract.analyze_document(
        Document={'Bytes': image_bytes},
        FeatureTypes=['TABLES']
    )

    return jsonify(response)

if __name__ == '__main__':
    app.run()

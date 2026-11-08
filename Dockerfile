FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=3000
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY admin.py artwork.py colours.py calculator.py config.py database.py keyboards.py main.py order.py shop.py texts.py ./
COPY miniapp/ ./miniapp/
COPY photos/ ./photos/
COPY templates/ ./templates/
EXPOSE 3000
CMD ["python", "main.py"]

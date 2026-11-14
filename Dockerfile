FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=3000 KROOG_AUTOUPDATE=1
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY autodeploy.py admin.py artwork.py colours.py calculator.py config.py database.py keyboards.py main.py order.py shop.py texts.py vinyl_previews.py ./
COPY miniapp/ ./miniapp/
COPY photos/ ./photos/
COPY templates/ ./templates/
EXPOSE 3000
CMD ["python", "autodeploy.py"]

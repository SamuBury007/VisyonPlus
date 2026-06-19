# Usa l'immagine ufficiale con Python e i browser già configurati da Microsoft
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Imposta la directory di lavoro nel container
WORKDIR /app

# Copia e installa i pacchetti Python necessari
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto il codice dell'applicazione nella cartella di lavoro
COPY . .

# Specifica la porta di ascolto
EXPOSE 8080

# Comando di avvio modificato per puntare a vidsrc_extractor:app
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "vidsrc_extractor:app"]

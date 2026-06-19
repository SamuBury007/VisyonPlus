FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app
COPY . .

RUN pip install flask requests playwright

ENV PORT=8080
EXPOSE 8080

CMD ["python", "vidsrc_extractor.py"]

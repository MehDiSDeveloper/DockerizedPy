FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
# --only-binary=:all: keeps a missing wheel from silently turning into a
# source build: without a compiler in the image that would fail late and
# confusingly, and installing one is what made the build time out.
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY . .

RUN chmod +x /app/start.sh

EXPOSE 8000

CMD ["/app/start.sh"]

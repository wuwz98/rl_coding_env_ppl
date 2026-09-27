FROM hub.byted.org/alpine:3.20
RUN apk add --no-cache python3
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
COPY src/stats.py ./stats.py
COPY public_tests/ ./tests/
COPY run_tests.py ./run_tests.py
RUN python -m unittest discover -s tests -v
CMD ["python", "run_tests.py", "--expect", "3"]

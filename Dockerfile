FROM python:3.11-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port for health checks
EXPOSE 8080

# Set environment variable to indicate we're running in container
ENV IN_CONTAINER=1

# Command to run the application
CMD ["python", "main.py"] 
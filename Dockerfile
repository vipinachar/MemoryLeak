FROM python:3.11
RUN apt-get update
RUN apt-get install python3-pip  -y
COPY . . 
RUN pip3 install -r requirements.txt 
CMD flask --app app --debug run --port=8080 --host 0.0.0.0
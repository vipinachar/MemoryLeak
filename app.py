from asyncio.subprocess import Process
from concurrent.futures import thread
from os import name
from flask import Flask, request, render_template
import json
import requests
import pyshorteners
import re
import numpy as np
import datetime
from datetime import timezone
import dateutil.relativedelta
import logging
from multiprocessing import Process, Manager, managers
import time
from multiprocessing import Process, Manager
import paramiko
import os
import sys

from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium import webdriver
from selenium.webdriver.common.by import By

# Setup Logger
file = "memory-leak.log"
logging.basicConfig(filename=file, format='%(asctime)s %(message)s',filemode='w')
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
pod_restarts=[["Namespace", "Pod name", "Restarts"]]  
app = Flask(__name__)  

# Post request to Grafana API 
def postRequest(grafana_dashboard_url, From, To, expression, intervalMS, grafana_username, grafana_password):
    data = {
        "queries":[
            {
                "expr": expression,
                "format":"time_series",
                "intervalFactor":2,
                "step":10,
                "refId":"A",
                "datasource": {
                    "type":"prometheus",
                    "uid":"prometheus"
                },
                "interval":"",
                "editorMode":"code",
                "range":True,
                "instant":True,
                "queryType":"timeSeriesQuery",
                "exemplar":False,
                "utcOffsetSec":0,
                "legendFormat":"",
                "datasourceId":1,
                "intervalMs":intervalMS,
                "maxDataPoints":1337
            }
        ],
    "from": From,
    "to":To
    }
    if grafana_dashboard_url.endswith("/") != True:
        grafana_dashboard_url = "%s/" % (str(grafana_dashboard_url))

    url = str(grafana_dashboard_url)+"api/ds/query"
    json_data = json.dumps(data)
    headers = {'content-type': 'application/json'}
    resp = requests.post(url, json_data, headers=headers, auth=(grafana_username, grafana_password))
    if resp.status_code == 200:
        return resp.json()
    else:
        return False

def checkIntervalMs(day):
    if day <= 1:
        intervalMs = 60000
    elif day <=3:
        intervalMs = 120000
    elif day <= 7:
        intervalMs = 300000
    elif day <= 11:
        intervalMs = 600000
    elif day <= 16:
        intervalMs = 900000
    elif day <= 23:
        intervalMs = 1200000
    else:
        intervalMs = 1800000
    return intervalMs

def timeStampToEpoch(timeStamp):
    list = timeStamp.split("-")
    year = int(list[0])
    month = int(list[1])
    list = str(list[2]).split()
    date=int(list[0])
    time = str(list[1]).split(":")
    hour = int(time[0])
    minute = int(time[1])
    second = int(time[2])
    epoch = str(int(datetime.datetime(year,month,date,hour,minute,second).timestamp()) * 1000)
    return epoch

def getPods(grafana_dashboard_url,epochFrom, epochTo, intervalMs, pod_list, grafana_username, grafana_password):
     # retrive pod_name and namespace 
    pods = postRequest(grafana_dashboard_url,epochFrom,epochTo,"node_namespace_pod:kube_pod_info:{namespace!~\"kube-system|monitoring|loki|calico-system|tigera-operator\"}", intervalMs, grafana_username, grafana_password)
    if pods == {'results': {'A': {'status': 200}}}:
        err_msg = "pods = {'results': {'A': {'status': 200}}}"
        logger.error(err_msg)
        return render_template("index.html", err_msg=err_msg, DashboardURL=grafana_dashboard_url,DurationFrom=epochFrom,DurationTo=epochTo)
    elif pods == False:
        err_msg = "pods = False"
        logger.error(err_msg)
        return render_template("index.html", err_msg=err_msg, DashboardURL=grafana_dashboard_url,DurationFrom=epochFrom,DurationTo=epochTo)
    else:
        for frame in pods["results"]["A"]["frames"]:
            pod_name = frame["schema"]["fields"][1]["labels"]["pod"]
            namespace = frame["schema"]["fields"][1]["labels"]["namespace"]

            if [namespace,pod_name] not in pod_list:
                logger.debug("Adding pod %s from namespace %s to pod list" % (pod_name, namespace))
                pod_list.append([namespace, pod_name])


def getMemorySpikes(grafana_dashboard_url,epochFrom, epochTo, intervalMs, namespace, pod_name, pod_information, memory_spikes, grafana_username, grafana_password):
        logger.debug("Checking memory utilization of pod %s in namespace %s" % (pod_name, namespace))
        print("Checking memory utilization of pod %s in namespace %s" % (pod_name, namespace))       
        expr = "(sum(container_memory_working_set_bytes{job=\"kubelet\", metrics_path=\"/metrics/cadvisor\", cluster=\"\", namespace=\"%s\", container!=\"\", image!=\"\", pod=\"%s\"}) by (pod))/1048646.93" % (namespace, pod_name)
        data = postRequest(grafana_dashboard_url,epochFrom, epochTo,expr, intervalMs, grafana_username, grafana_password)
        if data == {'results': {'A': {'status': 200}}}:
            err_msg = "data = {'results': {'A': {'status': 200}}}"
            logger.error(err_msg)
            return
        elif data == False:
            err_msg = "data = False"
            logger.error(err_msg)
            return     

        array = data['results']['A']['frames'][1]['data']['values'][1]
 
        starting_value = round(array[0],1)
        ending_value = round(array[-1],1)
        minimum_value = round(min(array),1)
        maximum_value = round(max(array),1)
        average_value = round(round(sum(array),1)/len(array),1)
        percentile_value = round(np.percentile(array, 95),1)
        logger.debug("Starting MiB : %s" % (starting_value))
        logger.debug("Ending MiB : %s" % (ending_value))
        logger.debug("Minimum MiB : %s" % (minimum_value))
        logger.debug("Maximum MiB : %s" % (maximum_value))
        logger.debug("Average MiB : %s" % (average_value))
        logger.debug("95 Percentile MiB : %s" % (percentile_value))

        pod_information.append([namespace, pod_name, maximum_value, average_value, percentile_value])
        
        if maximum_value >= (minimum_value*2) and maximum_value > 200:
            short_dashboard_url = str("{}/d/6581e46e4e5c7ba40a07646395ef7b23/kubernetes-compute-resources-pod?orgId=1&refresh=10s&from={}&to={}&viewPanel=4&var-datasource=default&var-cluster=&var-namespace={}&var-pod={}".format(grafana_dashboard_url, epochFrom, epochTo, namespace, pod_name))
            type_tiny = pyshorteners.Shortener()
            short_url = type_tiny.tinyurl.short(short_dashboard_url)

            grafana_embedding_url = str("{}/d-solo/6581e46e4e5c7ba40a07646395ef7b23/kubernetes-compute-resources-pod?orgId=1&refresh=10s&var-datasource=default&var-cluster=&var-namespace={}&var-pod={}&from={}&to={}&panelId=4".format(grafana_dashboard_url, namespace, pod_name,  epochFrom, epochTo))
            
            memory_spikes.append([namespace, pod_name, minimum_value, maximum_value, starting_value,ending_value, average_value, short_url, grafana_embedding_url])

def getPodRestarts(grafana_dashboard_url,epochFrom, epochTo, intervalMs, namespace, pod_name, pod_restarts, grafana_username, grafana_password):
        logger.debug("Checking pod restarts for pod %s in namespace %s" % (pod_name, namespace))

        expr = "kube_pod_container_status_restarts_total{pod=\"%s\"}" % (pod_name)

        data = postRequest(grafana_dashboard_url,epochFrom, epochTo,expr, intervalMs, grafana_username, grafana_password)

        if data == {'results': {'A': {'status': 200}}}:
            err_msg = "data = {'results': {'A': {'status': 200}}}"
            logger.error(err_msg)
            return
        elif data == False:
            err_msg = "data = False"
            logger.error(err_msg)
            return   

        if namespace == "scan-link-system":
            pod_restart = data['results']['A']['frames'][1]['data']['values'][1] 
        else: 
            pod_restart = data['results']['A']['frames'][0]['data']['values'][1]  

        logger.debug("Pod restarts for pod %s : %s" % (pod_name, str(pod_restart)))

        pod_restart = pod_restart[0]
        if int(pod_restart) > 0:
            loki_url = grafana_dashboard_url.split(".")
            loki_url[0] = "http://loki"
            loki_url = '.'.join(loki_url)
            short_dashboard_url = str(loki_url)+"/explore?orgId=1&left=%5B%22"+epochFrom+"%22,%22"+epochTo+"%22,%22Loki%22,%7B%22refId%22:%22A%22,%22expr%22:%22%7Bnamespace%3D%5C%22"+namespace+"%5C%22,pod%3D%5C%22"+pod_name+"%5C%22%7D%22%7D%5D"
            type_tiny = pyshorteners.Shortener()
            short_url = type_tiny.tinyurl.short(short_dashboard_url)
            pod_restarts.append([namespace, pod_name, pod_restart, short_url])   



def getNodeMemoryUtilization(grafana_dashboard_url,epochFrom, epochTo, intervalMs, node_memory_usage, grafana_username, grafana_password):
    logger.debug("Retrieving Node memory utlization")
    expr = "(instance:node_memory_utilisation:ratio{job=\"node-exporter\", instance!=\"\", cluster=\"\"} != 0)*100"
    data = postRequest(grafana_dashboard_url,epochFrom, epochTo,expr, intervalMs, grafana_username, grafana_password)
    if data == {'results': {'A': {'status': 200}}}:
        err_msg = "data = {'results': {'A': {'status': 200}}}"
        logger.error(err_msg)
        return
    elif data == False:
        err_msg = "data = False"
        logger.error(err_msg)
        return

    series = data['results']['A']['frames']
    number_of_nodes = len(series)//2

    for i in range (number_of_nodes, len(series)):
        try:
            instance = data['results']['A']['frames'][i]['schema']['fields'][1]['labels']['instance']
        except:
            instance = (data['results']['A']['frames'][i]['schema']['name'].split(',')[2].split('"')[1])
        memory = (data['results']['A']['frames'][i]['data']['values'][1])
        max_memory = str(round(max(memory),2))
        min_memory = str(round(min(memory),2))
        avg_memory = str(round(sum(memory)//len(memory),2))
        node_memory_usage.append([instance, max_memory, min_memory, avg_memory])

def getNodeCpuUtilization(grafana_dashboard_url,epochFrom, epochTo, intervalMs, node_cpu_usage, grafana_username, grafana_password):
    logger.debug("Retrieving Node cpu utlization")
    expr = "(instance:node_cpu_utilisation:rate5m{job=\"node-exporter\", instance!=\"\", cluster=\"\"} != 0)*100"
    data = postRequest(grafana_dashboard_url,epochFrom, epochTo,expr, intervalMs, grafana_username, grafana_password)
    if data == {'results': {'A': {'status': 200}}}:
        err_msg = "data = {'results': {'A': {'status': 200}}}"
        logger.error(err_msg)
        return
    elif data == False:
        err_msg = "data = False"
        logger.error(err_msg)
        return

    series = data['results']['A']['frames']
    number_of_nodes = len(series)//2

    for i in range (number_of_nodes, len(series)):
        try:
            instance = data['results']['A']['frames'][i]['schema']['fields'][1]['labels']['instance']
        except:
            instance = (data['results']['A']['frames'][i]['schema']['name'].split(',')[2].split('"')[1])
        memory = (data['results']['A']['frames'][i]['data']['values'][1])
        max_memory = str(round(max(memory),2))
        min_memory = str(round(min(memory),2))
        avg_memory = str(round(sum(memory)//len(memory),2))
        node_cpu_usage.append([instance, max_memory, min_memory, avg_memory])

@app.route('/', methods =["GET"])
def main():
    return render_template("index.html")

@app.route('/memory-leak', methods =["GET", "POST"])
def gfg():
    with Manager() as manager:
        if request.method == "POST":
            start_time = time.time()
            grafana_dashboard_url = request.form.get("grafana_dashboard_url")
            From = request.form.get("From")
            To = request.form.get("To")
            grafana_username = request.form.get("username")
            grafana_password = request.form.get("password")

            logger.debug("Grafana dashboard url : %s" % (grafana_dashboard_url))
            logger.debug("Grafana dashboard from time : %s" % (From))
            logger.debug("Grafana dashboard to time : %s" % (To))

            regexp = re.compile("[0-9]+-[0-9]+-[0-9]+ [0-9]+:[0-9]+:[0-9]+")
            if regexp.match(From):
                epochFrom = timeStampToEpoch(From)
            elif "now-" in From:
                epochFrom = From
                if "h" in From or "m" in From:
                    intervalMs = checkIntervalMs(1)
                elif "d" in From:
                    day=int(str(From.split("-")[1]).split("d")[0])
                    intervalMs = checkIntervalMs(day)
            else:
                err_msg = "Please use now-6d or YYYY-MM-DD HH:MM:SS time format"
                return render_template("index.html", err_msg=err_msg, DashboardURL=grafana_dashboard_url,DurationFrom=From,DurationTo=To)

            if regexp.match(To):
                epochTo = timeStampToEpoch(To)
            elif "now" in To: 
                epochTo = To
            else:
                err_msg = "Please use now-6d or YYYY-MM-DD HH:MM:SS time format"
                return render_template("index.html", err_msg=err_msg, DashboardURL=grafana_dashboard_url,DurationFrom=From,DurationTo=To)
        
            if regexp.match(From) and regexp.match(To):
                toDay = datetime.datetime.fromtimestamp(int(epochTo)/1000) 
                fromDay = datetime.datetime.fromtimestamp(int(epochFrom)/1000) 
                rd = dateutil.relativedelta.relativedelta (toDay, fromDay)
                intervalMs = checkIntervalMs(rd.days)

            logger.debug("epoch : %s of %s" % (epochFrom, From))
            logger.debug("epoch : %s of %s" % (epochTo, To))
            logger.debug("interval MS : %s " % (intervalMs))

            processes = []

            pod_list = manager.list()
            getPodListProcess = Process(target=getPods, args=(grafana_dashboard_url, epochFrom, epochTo, intervalMs, pod_list, grafana_username, grafana_password))
            getPodListProcess.start()

            # collect node memory usage
            node_memory_usage = manager.list()
            process = Process(target=getNodeMemoryUtilization,args=(grafana_dashboard_url, epochFrom, epochTo, intervalMs,node_memory_usage, grafana_username, grafana_password))
            process.start()
            processes.append(process)

            # collect node cpu usage
            node_cpu_usage = manager.list()
            process = Process(target=getNodeCpuUtilization,args=(grafana_dashboard_url, epochFrom, epochTo, intervalMs,node_cpu_usage, grafana_username, grafana_password))
            process.start()
            processes.append(process)
            
            getPodListProcess.join()

            # collect memory spikes
            memory_spikes = manager.list()
            pod_information = manager.list()
            for pod in pod_list:
                process = Process(target=getMemorySpikes, args=(grafana_dashboard_url, epochFrom, epochTo, intervalMs, pod[0], pod[1], pod_information, memory_spikes, grafana_username, grafana_password))
                process.start()
                processes.append(process)

            # collect pod restart data
            pod_restarts = manager.list()
            for pod in pod_list:
                process = Process(target=getPodRestarts, args=(grafana_dashboard_url, epochFrom, epochTo, intervalMs, pod[0], pod[1], pod_restarts, grafana_username, grafana_password))
                process.start()
                processes.append(process)

            for process in processes:
                process.join()

            logger.debug("--- %s seconds ---" % (time.time() - start_time))
            print("--- %s seconds ---" % (time.time() - start_time))
            memory_spikes.sort()
            pod_restarts.sort()
            return render_template("index.html", my_list=memory_spikes, pod_info=pod_information, DashboardURL=grafana_dashboard_url,DurationFrom=From,DurationTo=To, Interval=intervalMs, PodRestart=pod_restarts, NodeMemoryUsage=node_memory_usage, NodeCpuUsage=node_cpu_usage)

if __name__=='__main__':
   app.run()

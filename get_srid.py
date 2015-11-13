#/usr/bin/env python
import sys
import requests

shape_path = sys.argv[1]
prj_path = shape_path.split('.')[0] + '.prj'
with open(prj_path, 'r') as prj:
    prj_text = prj.read()

url = 'http://prj2epsg.org/search.json'
r = requests.get(url, params={'terms': prj_text})
srid = r.json().get('codes')[0].get('code')
sys.stdout.write(srid)

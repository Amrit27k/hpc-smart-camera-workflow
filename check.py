import xml.etree.ElementTree as ET
tree = ET.parse('pipeline_output/sumo/routes_city.xml')
vehs = [(v.get('depart'), v.get('id')) for v in tree.findall('vehicle')]
vehs.sort(key=lambda x: float(x[0]))
print('First 5 departures:')
for d,i in vehs[:5]:
    print(f'  vehicle {i} departs at t={d}s')
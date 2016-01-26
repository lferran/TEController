"""
Default configuration file
"""
#Package path
PPATH = '/root/TEController/'

# Hostname of the Traffic Engineering Controller host in the network.
LBC_hostname = 'c3'

# Hostname of the Traffic Generator host in the network.
TG_hostname = 'c2'

#Port on which the JSON-aware thread of the LBC is listening
LBC_JsonPort = 5000

# Place where the topology information is being stored inside the vm
# (and the hosts)
DB_path = '/tmp/db.topo'

# Path of the Traffic Generator package
TG_path = PPATH + 'tecontroller/trafficgenerator/'

# Path to the Traffic Engineering controller package
LBC_path = PPATH + 'tecontroller/loadbalancer/'

# Default port for the json-daemons for the hosts in the network
Hosts_JsonPort = 5000

# Path to the file where the flows definition for the Traffic
# Generator are stored
FlowFile = TG_path + 'flowfile.csv'

# Waiting time (in seconds) for hosts to check their IP
InitialWaitingTime = 10 

# Default port for which IPERF server is listening in the custom hosts
Hosts_DefaultIperfPort = '5001'

# Log folder for the hosts
Hosts_LogFolder = TG_path + "logs/"
const os = require('os');
const dummy = {
  lo: [{ address: '127.0.0.1', netmask: '255.0.0.0', family: 'IPv4', mac: '00:00:00:00:00:00', internal: true, cidr: '127.0.0.1/8' }],
  eth0: [{ address: '172.28.1.2', netmask: '255.255.240.0', family: 'IPv4', mac: '00:15:5d:00:00:01', internal: false, cidr: '172.28.1.2/20' }]
};
os.networkInterfaces = () => dummy;

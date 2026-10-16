// Preloaded via NODE_OPTIONS by the web scripts.
//
// Cause: on some WSL 2 setups (mirrored networking, VPN adapters, Hyper-V virtual switches) libuv's
// uv_interface_addresses() fails and Node's os.networkInterfaces() throws ("Unknown system error 13" /
// "A system error occurred: uv_interface_addresses returned Unknown system error"). The Angular CLI / Vite dev server
// call it to print the network URLs and crash. Check with: node -e "console.log(require('os').networkInterfaces())"
//
// The real function is kept whenever it works; only a throwing call falls back to a loopback-only answer.
const os = require('os');
const real = os.networkInterfaces.bind(os);
const loopback = {
  lo: [{ address: '127.0.0.1', netmask: '255.0.0.0', family: 'IPv4', mac: '00:00:00:00:00:00', internal: true, cidr: '127.0.0.1/8' }],
};
os.networkInterfaces = () => {
  try {
    return real();
  } catch {
    return loopback;
  }
};

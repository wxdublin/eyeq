from fabric.api import *

l1 = "l1"
l2 = "l2"
l3 = "l3"
root = [l1, l2, l3]

vm11 = "192.168.2.209"
vm12 = "192.168.2.210"

vm21 = "192.168.2.208"
vm22 = "192.168.2.206"

vm31 = "192.168.2.211"
vm32 = "192.168.2.212"
vm33 = "192.168.2.213"

# On 1G network
if 1:
    vm11 = "192.168.1.212"
    vm12 = "192.168.1.213"

    vm21 = "192.168.1.221"
    vm22 = "192.168.1.222"

    vm31 = "192.168.1.231"
    vm32 = "192.168.1.232"
    vm33 = "192.168.1.233"

guest = [vm11, vm12, vm21, vm22, vm31, vm32, vm33]

iface = {
  l1: "eth2",
  l2: "eth2",
  l3: "eth3",

  # 1G
  l1: "eth1",
  l2: "eth1",
  l3: "eth0",

  vm31 : "eth0", # l3
  vm32 : "eth0", # l3
  vm33 : "eth0", # l3

  vm21 : "eth0", # l2
  vm22 : "eth0", # l2

  vm11 : "eth0", # l1
  vm12 : 'eth0', # l1
}

env.roledefs = {
  'root': root,
  'guest': guest,
  'src': [vm31, vm32, vm21],
  'dst': [vm11, vm12, vm32],
}

env.always_use_pty = False

allhosts = root + guest

scripts = [
  # (path, perm)
  ('/usr/local/bin/iperf', "+x"),
  ('/usr/bin/killall', "+x"),
  ('/sbin/ethtool', "+x"),
]

# Tenants
tenants = {
  l1: [vm11, vm12],
  l2: [vm21, vm22],
  l3: [vm31, vm32, vm33]
}

tenant_weights = {
  l1: {vm11: 1, vm12: 4},
  l2: {vm21: 1, vm22: 4},
  l3: {vm31: 1, vm32: 1, vm33: 4}
}

# Traffic matrix
dst_ip = {
  vm31: vm11, # tenant A
  vm32: vm11, # tenant A
  vm33: vm11, # tenant A

  vm21: vm12  # tenant B
}

udp_opt = '-u -b 9G -l 60k'
tcp_opt = '-P 4 -l 32k'
iperf_opts = {
  vm31: udp_opt,
  vm32: udp_opt,
  vm33: udp_opt,
}

# Backgrounding
def _runbg(command, out_file="/dev/null", err_file=None, shell=True, pty=False):
    run('nohup %s >%s 2>%s </dev/null &' % (command, out_file, err_file or '&1'), shell, pty)


@task
@hosts(allhosts)
def ifconfig():
    run("ifconfig %s" % iface[env.host])

@task
@roles('guest')
@parallel
def txq():
    eth = iface[env.host]
    run("ifconfig %s txqueuelen 32" % eth)
    run("ifconfig %s | egrep -o 'txqueuelen:[0-9]+'" % eth)

@task
@roles('guest')
@parallel
def apt():
    sources = '/etc/apt/sources.list'
    put(sources, sources)
    # More reliable
    run("echo 'prepend domain-name-servers 8.8.8.8;' >> /etc/dhcp3/dhclient.conf")
    run("dhclient eth0")
    #run("echo nameserver 8.8.8.8 >> /etc/resolv.conf")

@task
@roles('guest')
@parallel
def nfs_install():
    run("apt-get -y install nfs-kernel-server nfs-common portmap")

@task
@roles('guest')
@parallel(pool_size=5)
def copy_scripts():
    for f,perm in scripts:
        put(f, f)
        run("chmod %s %s" % (perm, f))

@task
@roles('guest')
def test():
    run("iperf -h")

@task
@roles('dst')
@parallel
def start_servers():
    _runbg("iperf -s")

@task
@roles('src')
@parallel
def start_clients():
    dst = dst_ip[env.host]
    o = iperf_opts.get(env.host, '')
    run("iperf -c %s -t 20 -i 1 %s" % (dst, o))

@task
@roles('guest')
@parallel
def stop():
    env.warn_only = True
    run("kill -9 `pgrep iperf`")

@task
@roles('root')
@parallel
def setup():
    eth = iface[env.host]
    run("insmod ~/vimal/exports/perfiso.ko iso_param_dev=p%s" % eth)
    for ip in tenants[env.host]:
        wt = tenant_weights[env.host][ip]
        create_ip_tenant(ip, wt)
    set_1g_params()

@task
@roles('root')
@parallel
def remove():
    run("rmmod perfiso")

@task
@roles('root')
@parallel
def set_10G():
    eth = iface[env.host]
    run("ethtool -s %s speed 10000 duplex full" % eth)

def create_ip_tenant(ip, w=1):
    d = '/sys/module/perfiso/parameters'
    run("echo %s > %s/create_txc" % (ip, d))
    run("echo %s > %s/create_vq" % (ip, d))
    run("echo associate txc %s vq %s > %s/assoc_txc_vq" % (ip, ip, d))
    run("echo %s weight %s > %s/set_vq_weight" % (ip, w, d))

def set_param(name, value):
    run("echo %s > /proc/sys/perfiso/%s" % (value, name))

def set_1g_params():
    set_param("ISO_MAX_TX_RATE", "1000")
    set_param("ISO_TOKENBUCKET_TIMEOUT_NS", "100")
    set_param("ISO_VQ_MARK_THRESH_BYTES", "30000")
    set_param("ISO_VQ_DRAIN_RATE_MBPS", "900")
    set_param("ISO_VQ_UPDATE_INTERVAL_US", "1")
    set_param("ISO_MIN_BURST_BYTES", "3000")

@task
@hosts([vm21])
def run_nfs_client():
    run("~/vimal/nfs-xput.sh")

@task
@hosts([vm11])
def run_cross_traffic():
    _runbg("iperf -c %s -t 100 -i 2 -b 2G" % vm32)

@task
@parallel
def run_nfs_test():
    execute(run_cross_traffic)
    execute(run_nfs_client)

# Doesn't work for emulex NICs
@task
@roles('root')
@parallel
def set_1G():
    eth = iface[env.host]
    run("ethtool -s %s speed 1000 duplex full" % eth)

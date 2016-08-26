#!/usr/bin/env python

import sys

from oslo_config import cfg
from oslo_log import log as logging
import shade
from shade.exc import OpenStackCloudException

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

logging.register_options(CONF)
logging.setup(CONF, "")


class Designator(object):
    def __init__(self, cloud_name):
        self.cloud = shade.openstack_cloud(cloud=cloud_name)

    def subnet_lookup(self):
        self.subnets = dict()
        for network in self.cloud.list_networks():
            if network['dns_domain']:
                for subnet in network['subnets']:
                    self.subnets[subnet] = network['dns_domain']

    def port_lookup(self):
        self.ports = dict()
        for port in self.cloud.list_ports():
            if port['dns_name']:
                for ip in port['fixed_ips']:
                    dns_domain = self.subnets.get(ip['subnet_id'])
                    if dns_domain:
                        break
                if not dns_domain:
                    LOG.info(
                        'Not setting DNS info for port {} because dns_domain '
                        'is not set for network.'.format(port['id'])
                    )
                    continue
                self.ports[port['fixed_ips'][0]['ip_address']] = {
                    'domain': dns_domain,
                    'name': port['dns_name'],
                }

    def recordsets_lookup(self):
        self.recordsets = dict()
        for ip, dnsinfo in self.ports.items():
            if self.recordsets.get(dnsinfo['domain']):
                LOG.debug(
                    'Already fetched info for "{}"'.format(dnsinfo['domain']))
                continue
            try:
                self.recordsets.update(
                    self.crunch_recordsets(dnsinfo['domain']))
            except OpenStackCloudException:
                LOG.error('Zone "{}" does not exist'.format(dnsinfo['name']))
                pass

    def crunch_recordsets(self, dns_domain):
        ret = dict()
        for record in self.cloud.list_recordsets(dns_domain):
            if record['type'] != 'A':
                continue
            dns_name, dns_domain = record['name'].split('.', 1)
            ret[record['records'][0]] = {
                'domain': dns_domain,
                'name': dns_name,
            }
        return ret

    def record_create(self, ip, name, domain):
        self.cloud.create_recordset(domain, name, 'A', [ip])
        fqdn = convert_to_fqdn(name, domain)
        fqdn_ptr = convert_to_fqdn(split_reverse_join(ip), 'in-addr.arpa.')
        self.cloud.create_recordset(fqdn_ptr.split('.', 1)[1],
                                    fqdn_ptr, 'PTR', [fqdn])

    def record_delete(self, ip, name, domain):
        fqdn = convert_to_fqdn(name, domain)
        self.cloud.delete_recordset(domain, fqdn)
        fqdn_ptr = convert_to_fqdn(split_reverse_join(ip), 'in-addr.arpa.')
        self.cloud.delete_recordset(fqdn_ptr.split('.', 1)[1], fqdn_ptr)



def convert_to_fqdn(name, domain):
    return name + '.' + domain


def split_reverse_join(f, delim='.'):
    r = f.split(delim)
    r.reverse()
    return delim.join(r)


def main():
    d = Designator('sam')
    d.subnet_lookup()
    d.port_lookup()
    d.recordsets_lookup()

    for ip, dnsinfo in d.ports.items():
        if d.recordsets.get(ip):
            LOG.debug('already added')
            continue
        LOG.info('adding {}'.format(ip))
        d.record_create(ip, **dnsinfo)

    for ip, dnsinfo in d.recordsets.items():
        if d.ports.get(ip):
            LOG.debug('exists {}'.format(ip))
            continue
        LOG.info('deleting {}'.format(ip))
        d.record_delete(ip, **dnsinfo)

if __name__ == '__main__':
    main()

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
            if 'dns_domain' not in network:
                LOG.error(
                    'The "dns_domain" key was not found in the network '
                    'object. Please ensure that the extention_driver "dns" is '
                    'enabled in ml2_conf.ini for the ml2 plugin.'
                )
                break
            if network['dns_domain']:
                for subnet in network['subnets']:
                    self.subnets[subnet] = network['dns_domain']

    def port_lookup(self):
        self.ports = dict()
        sports = self.cloud.list_ports()
        for port in sports:
            if 'dns_name' not in port:
                LOG.error(
                    'The "dns_name" key was not found in the port object. '
                    'Please ensure that the extention_driver "dns" is enabled '
                    'in ml2_conf.ini for the ml2 plugin.'
                )
                break
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
        self.zones = dict()
        self.recordsets = dict()
        original_domains = set()
        for ip, dnsinfo in self.ports.items():
            original_domains.add(dnsinfo['domain'])

        for domain in original_domains:
            if self.recordsets.get(domain):
                LOG.debug(
                    'Already fetched info for "{}"'.format(domain))
                continue
            try:
                self.recordsets.update(
                    self.crunch_recordsets(domain))
            except OpenStackCloudException:
                LOG.error('Zone "{}" does not exist'.format(domain))
                pass

    def crunch_recordsets(self, dns_domain):
        ret = dict()

        if not self.zones.get(dns_domain):
            self.zones[dns_domain] = self.cloud.list_recordsets(dns_domain)
            i = 0
        for record in self.zones[dns_domain]:
            i = i + 1
            if record['type'] != 'A':
                continue
            dns_name, dns_domain = record['name'].split('.', 1)
            # ret[record['records'][0]] = {
            ret[i] = {
                'domain': dns_domain,
                'name': dns_name,
                'ip': record['records'][0]
            }
        return ret

    def record_create(self, ip, name, domain):
        fqdn = convert_to_fqdn(name, domain)
        fqdn_ptr = convert_to_fqdn(split_reverse_join(ip), 'in-addr.arpa.')
        try:
            self.cloud.create_recordset(fqdn_ptr.split('.', 1)[1],
                                        fqdn_ptr, 'PTR', [fqdn])
        except OpenStackCloudException as e:
            LOG.warn(repr(e))
            pass
        try:
            self.cloud.create_recordset(domain, name, 'A', [ip])
        except OpenStackCloudException as e:
            LOG.warn(repr(e))
            pass

    def record_delete(self, ip, name, domain):
        fqdn = convert_to_fqdn(name, domain)
        fqdn_ptr = convert_to_fqdn(split_reverse_join(ip), 'in-addr.arpa.')
        try:
            self.cloud.delete_recordset(fqdn_ptr.split('.', 1)[1], fqdn_ptr)
        except OpenStackCloudException as e:
            LOG.warn(repr(e))
            pass
        try:
            self.cloud.delete_recordset(domain, fqdn)
        except OpenStackCloudException as e:
            LOG.warn(repr(e))
            pass


def convert_to_fqdn(name, domain):
    return name + '.' + domain


def split_reverse_join(f, delim='.'):
    r = f.split(delim)
    r.reverse()
    return delim.join(r)


def main():
    d = Designator('default')
    d.subnet_lookup()
    d.port_lookup()
    d.recordsets_lookup()

    for ip, dnsinfo in d.ports.items():
        try:
            if d.recordsets.get(ip):
                LOG.debug('already added')
                continue
            try:
                LOG.info('adding {}'.format(ip))
                d.record_create(ip, **dnsinfo)
            except OpenStackCloudException as e:
                LOG.warn(repr(e))
                pass
        except Exception as ex:
            LOG.warn(repr(ex))
            pass

    for i, dnsinfo in d.recordsets.items():
        try:
            port = d.ports.get(dnsinfo['ip'])
            if port:
                if port['name'] == dnsinfo['name']:
                    LOG.debug('exists {}'.format(dnsinfo['ip']))
                    continue
                else:
                    try:
                        LOG.info('deleting {}'.format(dnsinfo['ip']))
                        d.record_delete(**dnsinfo)
                    except OpenStackCloudException as e:
                        LOG.warn(repr(e))
                        pass
            else:
                try:
                    LOG.info('deleting {}'.format(dnsinfo['ip']))
                    d.record_delete(**dnsinfo)
                except OpenStackCloudException as e:
                    LOG.warn(repr(e))
                    pass
        except Exception as ex:
            LOG.warn(repr(ex))
            pass


if __name__ == '__main__':
    main()


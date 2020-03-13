#!/usr/bin/env python
#
# aaron.segura / @rev_dr
#

import os
import re
import openstack

from oslo_config import cfg
from oslo_log import log as logging

LOG = logging.getLogger(os.path.basename(__file__))
CONF = cfg.CONF

logging.register_options(CONF)
logging.setup(CONF, '')


class Designator(object):
    def __init__(self, cloud_name='default'):
        """ Connect to the cloud and populate some initial variables
            with objects that will be reused throughout the script.
        """
        self.cloud = openstack.connect(cloud=cloud_name)

        try:
            # Disregard ports that have no dns_name set, acquire currency
            self.ports = {port['id']:port for port in self.cloud.network.ports()
                          if port['dns_name'] != ''}
        except KeyError:
            raise RuntimeError(
                   'The "dns_name" key was not found in the port object. '
                   'Please ensure that the extention_driver "dns" is enabled '
                   'in ml2_conf.ini for the ml2 plugin.')

        self.subnets = {subnet['id']:subnet for subnet in self.cloud.network.subnets()}
        self.zones = {zone['id']:zone for zone in self.cloud.dns.zones()}
        self.recordsets = self.get_recordsets()
        self.subnet_zone_lookup = self.get_subnet_zones()

    def get_recordsets(self):
        """ Create dictionary mapping zones to their respective records """
        ret = {}
        for zone_id,zone in self.zones.items():
            recordset = self.cloud.dns.recordsets(zone_id)
            ret[zone['name']] = [x for x in recordset if x['type'] in ['A','PTR']]
        return ret

    def get_subnet_zones(self):
        """ Create dictionary where each subnet id points to its DNS zone.
            Zone is determined by the network containing the subnet.
        """
        ret = {}
        for network in self.cloud.list_networks():
            if 'dns_domain' not in network:
                raise RuntimeError(
                    'The "dns_domain" key was not found in the network '
                    'object. Please ensure that the extention_driver "dns" is '
                    'enabled in ml2_conf.ini for the ml2 plugin.')

            if network['dns_domain']:
                for subnet in network['subnets']:
                    ret[subnet] = network['dns_domain']
        return ret
      
    def forward_record_exists(self, port, fixed_ip):
        """ Test for existence of DNS A-record for given fixed_ip """
        dns_domain = self._domain(fixed_ip)
        if dns_domain is None:
            LOG.debug('Skipping {id} because subnet does not have an associated domain.'
                     .format(**port))
            return True
        else:
            fqdn = self._fqdn(port, fixed_ip)
            for record in self.recordsets[dns_domain]:
                if record['type'] == 'A' and \
                record['name'] == fqdn and \
                fixed_ip['ip_address'] in record.records:
                    LOG.debug('RECORD EXISTS: {} -> {}'.format(fqdn, fixed_ip['ip_address']))
                    return True
        return False

    def create_forward_record(self, port, fixed_ip):
        """ Create DNS A-record for the given fixed_ip """
        dns_domain = self._domain(fixed_ip)
        fqdn = self._fqdn(port, fixed_ip)
        zone = [z for _,z in self.zones.items() if z['name'] == dns_domain].pop()
        LOG.info('CREATING A: (port:{}) {} -> {}'.format(port['id'], fqdn, fixed_ip['ip_address']))
        try:
            self.cloud.create_recordset(zone, name=fqdn, recordset_type='A', records=[fixed_ip['ip_address']])
        except openstack.exceptions.ConflictException as err:
            LOG.error(err.message)

    def reverse_record_exists(self, port, fixed_ip):
        """ Test for existence of DNS PTR-record for given fixed_ip """
        arpa_domain = self._arpa_domain(fixed_ip)
        arpa = self._arpa(fixed_ip)
        fqdn = self._fqdn(port, fixed_ip)

        if arpa_domain not in self.recordsets:
            LOG.debug('Skipping {id} because there is no reverse lookup zone for it.'
                     .format(**port))
            return True
        else:
            for record in self.recordsets[arpa_domain]:
                if record['type'] == 'PTR' and \
                record['name'] == self._arpa(fixed_ip) and \
                fqdn in record.records:
                    LOG.debug('RECORD EXISTS: {} -> {}'.format(arpa, fqdn))
                    return True
        return False

    def create_reverse_record(self, port, fixed_ip):
        """ Create DNS PTR-record for the given fixed_ip """
        arpa_domain = self._arpa_domain(fixed_ip)
        fqdn = self._fqdn(port, fixed_ip)
        arpa = self._arpa(fixed_ip)
        zone = [z for _,z in self.zones.items() if z['name'] == arpa_domain].pop()
        LOG.info('CREATING PTR: (port:{}) {} -> {}'.format(port['id'], arpa, fqdn))
        self.cloud.create_recordset(zone, name=arpa, recordset_type='PTR', records=[fqdn])

    def record_port_exists(self, recordset, record):
        """ Given a recordset and record, verify a port:fixed_ip exists with the
            proper attributes
        """
        if recordset['type'] == 'A':
            dns_name = recordset['name'].replace('.'+recordset['zone_name'], '', 1)
            for _,port in self.ports.items():
                if dns_name == port['dns_name']:
                    for fixed_ip in port['fixed_ips']:
                        if record == fixed_ip['ip_address']:
                            return True

        if recordset['type'] == 'PTR':
            record_ip = self._arpa_to_ip(recordset['name'])
            for _,port in self.ports.items():
                for fixed_ip in port['fixed_ips']:
                    if record_ip == fixed_ip['ip_address']:
                        if re.match(port['dns_name'], record):
                            return True
        return False

    def remove_recordset(self, recordset):
        """ Remove the given recordset """
        LOG.info("REMOVING: (recordset:{}) {} {} -> {}".format(
                 recordset['id'],
                 recordset['type'],
                 recordset['name'],
                 recordset['records']))
        self.cloud.delete_recordset(recordset['zone_id'], recordset['id'])

    # Helper Functions
    #
    def _fqdn(self, port, fixed_ip):
        """ Return FQDN for a port object """
        return port['dns_name'] + '.' + self._domain(fixed_ip)

    def _domain(self, fixed_ip):
        """ Return domain name associated with a fixed_ip object """
        try:
            return self.subnet_zone_lookup[fixed_ip['subnet_id']]
        except KeyError:
            return None

    def _arpa_domain(self, fixed_ip):
        """ Return PTR domain for given fixed_ip object """
        return self._arpa(fixed_ip, domain=True)

    def _arpa(self, fixed_ip, domain=False):
        """ Return PTR record for given fixed_ip object """
        parts = fixed_ip['ip_address'].split('.')
        if domain:
            parts.pop()
        parts.reverse()
        return '.'.join(parts) + '.in-addr.arpa.'

    def _arpa_to_ip(self, arpa):
        """ Convert arpa record string to IP address
            Example: 52.16.64.10.in-addr.arpa. -> 10.64.16.52
        """
        parts = arpa.split('.')
        parts.reverse()        
        return '.'.join(parts[3:7])


def main():
    designator = Designator()

    # Remove stale records
    #
    for _,recordsets in designator.recordsets.items():
        for recordset in recordsets:
            for record in recordset.records:
                if not designator.record_port_exists(recordset, record):
                    designator.remove_recordset(recordset)

    # Create missing records
    #
    for _,port in designator.ports.items():
        for fixed_ip in port['fixed_ips']:
            if fixed_ip['subnet_id'] in designator.subnet_zone_lookup:
                if not designator.forward_record_exists(port, fixed_ip):
                    designator.create_forward_record(port, fixed_ip)
                if not designator.reverse_record_exists(port, fixed_ip):
                    designator.create_reverse_record(port, fixed_ip)


if __name__ == '__main__':
    try:
        LOG.info("Starting...")
        main()
        LOG.info("Finished!")
    except KeyboardInterrupt:
        print('\nCaught CTRL-C.')


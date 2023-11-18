import logging
from typing import Any

from django.core.cache import cache
from django.db import models

from domains.models import Domain
from subdomains.models import Subdomain
from .exceptions import DnsRecordBadRequestError, DnsRecordNotFoundError, DnsRecordProviderError
from .providers.base import BaseDnsRecordProvider


class Record(models.Model):
    class RecordType(models.TextChoices):
        A = 'A', 'A - a host address',
        NS = 'NS', 'NS - an authoritative name server',
        CNAME = 'CNAME', 'CNAME - the canonical name for an alias',
        MX = 'MX', 'MX - mail exchange',
        TXT = 'TXT', 'TXT - text strings',
        AAAA = 'AAAA', 'AAAA - IP6 Address',
        SRV = 'SRV', 'SRV - Server Selection'

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    provider_id = models.CharField(max_length=255, unique=True, null=True)

    subdomain_name = models.CharField(max_length=63)
    domain = models.ForeignKey(Domain, on_delete=models.RESTRICT)

    name = models.CharField('Name', max_length=63)
    ttl = models.IntegerField('TTL', default=3600)
    type = models.CharField('Type', max_length=10, choices=RecordType.choices)

    service = models.CharField('Service', max_length=63, null=True, help_text='Required for SRV record.')
    protocol = models.CharField('Protocol', max_length=63, null=True, help_text='Required for SRV record.')

    priority = models.IntegerField('Priority', null=True, help_text='Required for MX and SRV records.')
    weight = models.IntegerField('Weight', null=True, help_text='Required for SRV record.')
    port = models.IntegerField('Port', null=True, help_text='Required for SRV record.')

    target = models.CharField('Target', max_length=255)

    @property
    def full_name(self) -> str:
        return self.join_name(self.service, self.protocol, self.name)

    @property
    def data(self) -> str:
        return self.join_data(self.priority, self.weight, self.port, self.target)

    @property
    def subdomain(self) -> Subdomain:
        return Subdomain.objects.get(name=self.subdomain_name, domain=self.domain)

    def __str__(self):
        return f'{self.full_name} {self.ttl} IN {self.type} {self.data}'

    @classmethod
    def list_records(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain) -> list['Record']:
        if subdomain is None:
            return []
        cache_key = 'records:' + str(subdomain)
        cache_value = cache.get(cache_key)
        if cache_value is not None:
            return cache_value
        if provider:
            try:
                provider_records = provider.list_records(subdomain.name, subdomain.domain)
                provider_record_id_set = set(map(lambda x: x['provider_id'], provider_records))
                for record in cls.objects.filter(subdomain_name=subdomain.name):
                    if record.provider_id not in provider_record_id_set:
                        record.delete()
                record_dict = {provider_id: x for provider_id, x in
                               map(lambda x: (x.provider_id, x), cls.objects.filter(subdomain_name=subdomain.name))}
                for provider_record in provider_records:
                    provider_id = provider_record.get('provider_id')
                    if provider_id in record_dict:
                        record_dict.get(provider_id).update_by_provider_record(provider_record)
                        continue
                    provider_record.update({
                        'subdomain_name': subdomain.name,
                        'domain': subdomain.domain,
                    })
                    cls.objects.update_or_create(provider_id=provider_id, defaults=provider_record)
            except DnsRecordProviderError as e:
                logging.error(e)
        records = cls.objects.filter(subdomain_name=subdomain.name).order_by('type', 'name', '-id')
        cache.set(cache_key, records, timeout=3600)
        for record in records:
            cache.set('records:' + str(record.id), record, timeout=record.ttl)
        return records

    @classmethod
    def create_record(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain, **kwargs) -> 'Record':
        if not kwargs.get('name', '').endswith(subdomain.name):
            raise DnsRecordBadRequestError('Name is invalid.')
        if kwargs.get('type') in ('NS', 'CNAME', 'MX', 'SRV',) and not kwargs.get('target').endswith('.'):
            kwargs['target'] = kwargs.get('target') + '.'
        record = cls(subdomain_name=subdomain.name, domain=subdomain.domain, **kwargs)
        if provider:
            try:
                provider_record = provider.create_record(subdomain.name, subdomain.domain, **kwargs)
                record.provider_id = provider_record.get('provider_id')
            except DnsRecordProviderError as e:
                logging.error(e)
        record.save()
        cache.delete('records:' + str(subdomain))
        cache.set('records:' + str(record.id), record, timeout=record.ttl)
        return record

    @classmethod
    def retrieve_record(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain, id: int) -> 'Record':
        cache_key = 'records:' + str(id)
        cache_value = cache.get(cache_key,
                                next(filter(lambda x: x.id == id, cache.get('records:' + str(subdomain), [])), None))
        if cache_value is not None:
            return cache_value
        try:
            record = cls.objects.get(subdomain_name=subdomain.name, pk=id)
            if provider:
                try:
                    provider_record = provider.retrieve_record(subdomain.name, subdomain.domain, record.provider_id)
                    if provider_record is None:
                        record.delete()
                        record = None
                    else:
                        record.update_by_provider_record(provider_record)
                except DnsRecordProviderError as e:
                    logging.error(e)
            if record is None:
                cache.delete('records:' + str(subdomain))
                cache.delete('records:' + str(id))
            else:
                cache.set(cache_key, record, timeout=record.ttl)
            return record
        except cls.DoesNotExist:
            raise DnsRecordNotFoundError()

    @classmethod
    def update_record(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain, id: int, **kwargs) -> 'Record':
        if 'name' in kwargs.keys() and not kwargs.get('name', '').endswith(subdomain.name):
            raise DnsRecordBadRequestError('Name is invalid.')
        if kwargs.get('type') in ('NS', 'CNAME', 'MX', 'SRV',) and not kwargs.get('target').endswith('.'):
            kwargs['target'] = kwargs.get('target') + '.'
        try:
            record = cls.objects.get(subdomain_name=subdomain.name, pk=id)
            for k, v in kwargs.items():
                if k in ['name', 'type', 'service', 'protocol'] and v != getattr(record, k):
                    raise DnsRecordBadRequestError(f'{k.capitalize()} cannot be changed.')
                setattr(record, k, v)
            if provider:
                try:
                    provider.update_record(subdomain.name, subdomain.domain, record.provider_id, **kwargs)
                except DnsRecordProviderError as e:
                    logging.error(e)
            record.save()
            cache.delete('records:' + str(subdomain))
            cache.set('records:' + str(record.id), record, timeout=record.ttl)
            return record
        except cls.DoesNotExist:
            raise DnsRecordNotFoundError()

    @classmethod
    def delete_record(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain, id: int) -> None:
        try:
            record = cls.objects.get(subdomain_name=subdomain.name, pk=id)
            if provider:
                try:
                    provider.delete_record(subdomain.name, subdomain.domain, record.provider_id)
                except DnsRecordProviderError as e:
                    logging.error(e)
            record.delete()
            cache.delete('records:' + str(subdomain))
            cache.delete('records:' + str(id))
        except cls.DoesNotExist:
            raise DnsRecordNotFoundError()

    @classmethod
    def export_zone(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain) -> str:
        return '\n'.join(map(str, cls.list_records(provider, subdomain)))

    @classmethod
    def import_zone(cls, provider: BaseDnsRecordProvider | None, subdomain: Subdomain, zone: str) -> None:
        lines = list(filter(lambda x: x[0] != ';', map(lambda x: x.strip(), zone.splitlines())))
        for line in lines:
            cls.create_record(provider, subdomain, **cls.parse_record(line))

    @staticmethod
    def split_name(full_name: str) -> tuple[str | None, str | None, str]:
        names = full_name.split('.')
        service = names.pop(0) if len(names) >= 3 and names[0].startswith('_') else None
        protocol = names.pop(0) if len(names) >= 2 and names[0].startswith('_') else None
        name = '.'.join(names)
        return service, protocol, name

    @staticmethod
    def join_name(service: str | None, protocol: str | None, name: str) -> str:
        if service is not None and not service.startswith('_'):
            service = '_' + service
        if protocol is not None and not protocol.startswith('_'):
            protocol = '_' + protocol
        return '.'.join(filter(lambda x: x is not None, [service, protocol, name]))

    @staticmethod
    def split_data(data: str) -> tuple[int | None, int | None, int | None, str]:
        values = data.split()
        priority = int(values[0]) if len(values) > 1 else None
        weight = int(values[1]) if len(values) == 4 else None
        port = int(values[2]) if len(values) == 4 else None
        target = values[-1]
        return priority, weight, port, target

    @staticmethod
    def join_data(priority: int | None, weight: int | None, port: int | None, target: str) -> str:
        return ' '.join(map(str, filter(lambda x: x is not None, [priority, weight, port, target])))

    @classmethod
    def parse_record(cls, raw_record: str) -> dict[str, Any]:
        r = raw_record.split()
        service, protocol, name = cls.split_name(r[0])
        priority, weight, port, target = cls.split_data(r[-1])
        return {
            'name': name,
            'ttl': int(r[1]) if r[1] != 'IN' else int(r[2]),
            'type': r[3],
            'service': service,
            'protocol': protocol,
            'priority': priority,
            'weight': weight,
            'port': port,
            'target': target,
        }

    @classmethod
    def synchronize_records(cls, provider: BaseDnsRecordProvider) -> None:
        logging.info('Start synchronizing records.')
        for subdomain in Subdomain.objects.all():
            cls.list_records(provider, subdomain)
        logging.info('End synchronizing records.')

    def update_by_provider_record(self, provider_record: dict[str, Any]) -> bool:
        is_updated = False
        for k, v in provider_record.items():
            if getattr(self, k) != v:
                setattr(self, k, v)
                is_updated = True
        if is_updated:
            self.save()
        return is_updated

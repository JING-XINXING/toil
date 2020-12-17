#!/usr/bin/env python
"""
sdb_domain_and_bucket_domain.py

Convenience script to clean up extraneous test buckets and/or sbd domains that testing may have left behind.

Manually requires the user to inspect and submit "yes" or "y" to delete buckets/domains.

Failing or canceled tests may miss clean up, and leftover test buckets and sdb domains can build up.
Run this script occasionally or when needed (there are limits to the number of buckets/domains we can have and we
can hit those limits).
"""
import argparse
import boto3
import os
import re
import sys

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))  # noqa
sys.path.insert(0, pkg_root)  # noqa

from src.toil.lib.generatedEC2Lists import regionDict

# put us-west-2 first as our default test region; that way anything with a universal region shows there
regions = ['us-west-2'] + [region for region in regionDict if region != 'us-west-2']

# never show these buckets; never offer to delete them; never forget
absolutely_do_not_delete_these_buckets = ['318423852362-cgcloud',  # not sure what this is
                                          'aws-config20201211232942693800000001',  # AWS logging; ask Erich?
                                          'cgl-pipeline',  # something important?
                                          'cgl-rnaseq-recompute-fixed-toil',  # the 20,000 toil-rna-seq recompute data
                                          'toil-cloudtrail-bucket',  # also AWS logging... ?; ask Erich
                                          'toil-cwl-infra-test-bucket-dont-delete',  # test infra; never delete
                                          'toil-datasets',  # test infra; never delete
                                          'toil-no-location-bucket-dont-delete',  # test infra; never delete
                                          'toil-preserve-file-permissions-tests']  # test infra; never delete


def contains_uuid(string):
    """
    Determines if a string contains a pattern like: '28064c76-a491-43e7-9b50-da424f920354',
    which toil uses in its test generated bucket names.
    """
    return bool(re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}').findall(string))


def contains_num_only_uuid(string):
    """
    Determines if a string contains a pattern like: '13614-31311-31347',
    which toil uses in its test generated sdb domain names.
    """
    return bool(re.compile('[0-9]{5}-[0-9]{5}-[0-9]{5}').findall(string))


def contains_toil_test_patterns(string):
    return contains_uuid(string) or contains_num_only_uuid(string)


def delete_s3_bucket(bucket, region):
    print('==============================================')
    print(f'Deleting s3 bucket in {region}: {bucket}')
    print('==============================================')
    s3_client = boto3.client('s3', region_name=region)
    s3_resource = boto3.resource('s3', region_name=region)

    paginator = s3_client.get_paginator('list_object_versions')
    for response in paginator.paginate(Bucket=bucket):
        versions = response.get('Versions', []) + response.get('DeleteMarkers', [])
        for version in versions:
            print(f"    Deleting {version['Key']} version {version['VersionId']}")
            s3_client.delete_object(Bucket=bucket, Key=version['Key'], VersionId=version['VersionId'])
    s3_resource.Bucket(bucket).delete()
    print(f'\n * Deleted s3 bucket successfully: {bucket}\n\n')


def matches(resource_name):
    if resource_name.endswith('--files') or resource_name.endswith('--jobs'):
        if contains_toil_test_patterns(resource_name):
            return resource_name

    if resource_name.startswith('import-export-test-'):
        return resource_name


def find_buckets_to_cleanup(include_all, matching):
    buckets = dict()
    for region in regions:
        print(f'\n[{region}] Buckets:')
        try:
            s3_resource = boto3.resource('s3', region_name=region)
            buckets_in_region = find_buckets_in_region(s3_resource, include_all, matching)
            new_buckets = [b for b in buckets_in_region if b not in buckets]
            print('    ' + '\n    '.join(new_buckets))
            for bucket in new_buckets:
                buckets[bucket] = region
        except Exception as e:
            # Occurs with botocore.exceptions.ClientError.
            if 'Your account is not signed up for the S3 service' in str(e):
                print('    Your account is not signed up for the S3 service in this region.')
            else:
                print(f'    An error occurred in this region: {e}')
    return buckets


def find_sdb_domains_to_cleanup(include_all, matching):
    sdb_domains = dict()
    for region in regions:
        print(f'\n[{region}] SimpleDB Domains:')
        try:
            sdb_client = boto3.client('sdb', region_name=region)
            domains_in_region = find_sdb_domains_in_region(sdb_client, include_all, matching)
            new_domains = [b for b in domains_in_region if b not in sdb_domains]
            print('    ' + '\n    '.join(new_domains))
            for sdb_domain in new_domains:
                sdb_domains[sdb_domain] = region
        except Exception as e:
            # Occurs with botocore.exceptions.SSLError in regions that don't support SDB.
            # Don't hard-code supported regions, just in case AWS changes these, in order to avoid blind spots.
            if 'SSL validation failed' in str(e):
                print('    SimpleDB is not offered in this region.')
            else:
                print(f'    An error occurred in this region: {e}')
    return sdb_domains


def find_buckets_in_region(s3_resource, include_all, matching):
    buckets_to_cleanup = []
    for bucket in s3_resource.buckets.all():
        if bucket.name not in absolutely_do_not_delete_these_buckets:
            if matching:
                for m in matching:
                    if m in bucket.name:
                        buckets_to_cleanup.append(bucket.name)
            elif matches(bucket.name) or include_all:
                buckets_to_cleanup.append(bucket.name)
    return buckets_to_cleanup


def find_sdb_domains_in_region(sdb_client, include_all, matching):
    sdb_domains_to_cleanup = []
    for sdb_domain in sdb_client.list_domains().get('DomainNames', []):
        if matching:
            for m in matching:
                if m in sdb_domain:
                    sdb_domains_to_cleanup.append(sdb_domain)
        elif matches(sdb_domain) or include_all:
            sdb_domains_to_cleanup.append(sdb_domain)
    return sdb_domains_to_cleanup


def main(argv):
    parser = argparse.ArgumentParser(
        description='View and/or clean up s3 buckets and/or sdb domains in an AWS Account.')

    parser.add_argument("--include-all", dest='include_all', action='store_true', required=False,
                        help="Don't filter based on buckets/domains that look like test objects.  "
                             "List everything in the account.")
    parser.add_argument("--view-only", dest='view_only', action='store_true', required=False,
                        help="Don't ask to delete.  Just view everything.")
    parser.add_argument("--skip-buckets", dest='skip_buckets', action='store_true', required=False,
                        help="Skip doing anything with buckets.")
    parser.add_argument("--skip-sdb", dest='skip_sdb', action='store_true', required=False,
                        help="Skip doing anything with SimpleDB domains.")
    parser.add_argument("--matching", dest='matching', type=str, required=False,
                        help="Only return resources containing the comma-delimited keywords.  "
                             "For example, adding --matching='hello,goodbye' would return any "
                             "buckets or domains that include either 'hello' or 'goodbye'.")
    parser.set_defaults(view_only=False, include_all=False, skip_buckets=False, skip_sdb=False, matching='')

    options = parser.parse_args(argv)

    account_name = boto3.client('iam').list_account_aliases()['AccountAliases'][0]
    print(f'Now running for AWS account: {account_name}.')

    matching = [m.strip() for m in options.matching.split(',') if m.strip()]

    if matching and options.include_all:
        raise ValueError('Cannot filter on matching patterns AND include everything.  Please specify either '
                         '"--view-only" or "--matching", but not both.')

    if not options.skip_buckets:
        buckets = find_buckets_to_cleanup(options.include_all, matching)
        if not options.view_only:
            response = input(f'Do you wish to delete these buckets in account: {account_name}?  (Y)es (N)o: ')
            if response.lower() in ('y', 'yes'):
                print('\nOkay, now deleting...')
                for bucket, region in buckets.items():
                    delete_s3_bucket(bucket, region)

    if not options.skip_sdb:
        sdb_domains = find_sdb_domains_to_cleanup(options.include_all, matching)
        if not options.view_only:
            response = input(f'Do you wish to delete these SDB domains in account: {account_name}?  (Y)es (N)o: ')
            if response.lower() in ('y', 'yes'):
                print('\nOkay, now deleting...')
                for sdb_domain, region in sdb_domains.items():
                    sdb_client = boto3.client('sdb', region_name=region)
                    sdb_client.delete_domain(DomainName=sdb_domain)


if __name__ == '__main__':
    main(sys.argv[1:])

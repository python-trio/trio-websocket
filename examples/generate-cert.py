import pathlib
import sys

import trustme

def main():
    here = pathlib.Path(__file__).parent
    ca_path = here / 'fake.ca.pem'
    server_path = here / 'fake.server.pem'
    if ca_path.exists() and server_path.exists():
        print('The CA ceritificate and server certificate already exist.')
        sys.exit(1)
    print('Creating self-signed certificate for localhost/127.0.0.1:')
    ca_cert = trustme.CA()
    ca_cert.cert_pem.write_to_path(ca_path)
    print(f' * CA certificate: {ca_path}')
    server_cert = ca_cert.issue_server_cert('localhost', '127.0.0.1')
    server_cert.private_key_and_cert_chain_pem.write_to_path(server_path)
    print(f' * Server certificate: {server_path}')
    print('Done')


if __name__ == '__main__':
    main()

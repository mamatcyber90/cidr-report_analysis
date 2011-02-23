import multiprocessing
import Queue
import os.path
import datetime
import re
import aspath
import subprocess

class PrefixOrigin(object):
    def __init__(self, origin_as, date_effective, weeks_seen):
        self.origin_as = origin_as
        self.date_effective = date_effective
        self.weeks_seen = weeks_seen


def extract_rib_date(rib_path):
    rib_name = os.path.basename(rib_path)
    try:
        return datetime.datetime.strptime(
            re.search('(\d{4}\-\d{2}\-\d{2})', rib_name).groups()[0],
            '%Y-%m-%d').date()
    except AttributeError:
        return datetime.datetime.strptime(
            re.search('(\d{8})', rib_name).groups()[0], '%Y%m%d').date()


def get_peerid_factory(peermap, peermap_lock, local_peermap):
    def get_peerid(peer_as, peer_ip):
        peer_tuple = (peer_as, peer_ip)
        try:
            return local_peermap[peer_tuple]
        except KeyError:
            peermap_lock.acquire()
            assert len(peermap)+1 not in peermap.values()  # extra precaution
            peer_id = peermap.setdefault(peer_tuple, len(peermap)+1)
            peermap_lock.release()
            local_peermap[peer_tuple] = peer_id
            return peer_id
    return get_peerid


def process_txtrib_worker(
    txtrib_queue, peermap, peermap_lock, stdout_lock, use_db):
    local_peermap = {}
    if use_db:
        get_peerid = get_peerid_factory(peermap, peermap_lock, local_peermap)

    while True:
        debug_output = []
        try:
            txtrib_path = txtrib_queue.get_nowait()
        except Queue.Empty:
            return

        # filenames
        dir_name = os.path.dirname(txtrib_path)
        base_name = '.'.join(os.path.basename(txtrib_path).split('.')[:-1])
        full_path = os.path.join(dir_name, base_name)
        normrib_path = full_path + '.normrib'
        peers_path = full_path + '.peers'
        ppapp_path = full_path + '.ppapp' #ppapp = prefixes per as, per peer
        ## db files
        if use_db:
            peersum_path = full_path + '.peersum'
            peersum_file = open(peersum_path, 'w')
            peersum_file.write(
                "# generated on {0} UTC\n".format(datetime.datetime.utcnow()))
            peersum_file.write("# date,origin_as,peerid,ppapp\n")

            origins_path = full_path + '.origins'
            origins_file = open(origins_path, 'w')
            origins_file.write(
                "# generated on {0} UTC\n".format(datetime.datetime.utcnow()))
            origins_file.write("# prefix,peerid,origin_as,ordinal_date\n")

        if use_db:
            debug_output.append((
                "Postprocessing RIB and generating .normrib, "
                ".peers, .ppapp, .peersum, and .origins files for {0}."
                ).format(base_name))
        else:
            debug_output.append((
                "Postprocessing RIB and generating .normrib, "
                ".peers, and .ppapp files for {0}.").format(base_name))

        txtrib_file = open(txtrib_path)
        normrib_file = open(normrib_path, 'w')

        # ppapp and ppp are keyed on peer_ip, or peer asn if there is no peer_ip
        ppapp = {}  # prefixes per as, per peer
        ppp = {}  # prefixes per peer
        peer_asns = {}
        peer_as_cache = {} # keyed on peer ip
        origin_as_cache = {} # keyed on prefix

        date_sort = extract_rib_date(txtrib_path).toordinal()
        date_str = extract_rib_date(txtrib_path).strftime('%Y-%m-%d')

        for line in txtrib_file:
            try:
                components = line.split()
                [prefix, prefix_len] = components[0].split('/')
                peer_ip = components[1]
                raw_as_path = components[2:]
                if len(raw_as_path) == 1:
                    # find observer and peer ASNs -- we'll need these later
                    try:
                        if components[0] not in origin_as_cache:
                            output = subprocess.Popen(
                                "grep -P -m 1 '{0} (\d+\.)+\d+ \d+' {1}".format(
                                components[0], txtrib_path), shell=True,
                                stdout=subprocess.PIPE).communicate()[0]
                            origin_as_cache[components[0]] = output.split()[-1]
                        origin_as = origin_as_cache[components[0]]
                    except IndexError:
                        origin_as = None
                    try:
                        if peer_ip not in peer_as_cache:
                            output = subprocess.Popen(
                                "grep -P -m 1 '{0} \d+' {1}".format(
                                peer_ip, txtrib_path), shell=True,
                                stdout=subprocess.PIPE).communicate()[0]
                            peer_as_cache[peer_ip] = output.split()[2]
                        peer_as = peer_as_cache[peer_ip]
                    except IndexError:
                        peer_as = None

                    if raw_as_path[0] == '-' and peer_as and origin_as:
                        # '-' is a null AS_PATH in cisco terminology
                        raw_as_path = [peer_as, origin_as]
                        debug_output.append(
                            "NULL AS_PATH: replacing '{0}' with {1}".format(
                            line.strip(), raw_as_path))
                    elif peer_as and raw_as_path[0] == peer_as:
                        raw_as_path.append(origin_as)
                        debug_output.append(
                            "ORIGIN ASN ADDED: replacing '{0}' with {1}".format(
                            line.strip(), raw_as_path))
                    elif origin_as and raw_as_path[0] == origin_as:
                        raw_as_path.insert(0, peer_as)
                        debug_output.append(
                            "PEER ASN ADDED: replacing '{0}' with {1}".format(
                            line.strip(), raw_as_path))
                    else:
                        debug_output.append(
                            "UNKNOWN AS_PATH: ignoring '{0}".format(
                            line.strip()))
                        raise ValueError
                # TODO capture notes/output from normalize_as_path
                norm_path = aspath.normalize_as_path(raw_as_path)
                if norm_path:
                    norm_path.reverse()
            except (AttributeError, LookupError, ValueError):
                norm_path = None

            if norm_path:
                if use_db:
                    origins_file.write("{0},{1},{2},{3}\n".format(
                        '/'.join([prefix, prefix_len]),
                        get_peerid(norm_path[-1], peer_ip),
                        norm_path[0], date_sort))
                normrib_file.write("{0:<18} {1:<15} {2}\n".format(
                    '/'.join([prefix, prefix_len]), peer_ip,
                    aspath.path_to_string(norm_path)))
                try:
                    ppapp.setdefault(peer_ip, {})[norm_path[0]] += 1
                    ppp[peer_ip] += 1
                except KeyError:
                    ppapp.setdefault(peer_ip, {}).setdefault(norm_path[0], 1)
                    ppp.setdefault(peer_ip, 1)
                if peer_ip not in peer_asns:
                    peer_asns[peer_ip] = norm_path[-1]
            else:
                debug_output.append(
                    "INVALID AS_PATH: dropping '{0}'".format(line.strip()))

        normrib_file.close()
        txtrib_file.close()

        ppapp_file = open(ppapp_path,'w')
        ppapp_file.write("# prefix_count origin_as peer_as peer_ip\n")
        for peer_ip in ppapp:
            for origin_as in ppapp[peer_ip]:
                ppapp_file.write(
                    '{0:>8} {1:>10} {2:>5} {3:<15}\n'.format(
                    ppapp[peer_ip][origin_as], origin_as,
                    peer_asns[peer_ip], peer_ip))
                if use_db:
                    # TODO include notes
                    peerid = get_peerid(peer_asns[peer_ip], peer_ip)
                    peersum_file.write("{0},{1},{2},{3}\n".format(
                        date_str, origin_as, peerid, ppapp[peer_ip][origin_as]))
        ppapp_file.close()

        peers_file = open(peers_path,'w')
        peers_file.write("# prefix_count peer_as peer_ip\n")
        for peer_ip in ppp:
            peers_file.write('{0:>8} {1:>5} {2:<15}\n'.format(
                ppp[peer_ip], peer_asns[peer_ip], peer_ip))
        peers_file.close()

        if use_db:
            peersum_file.close()
            origins_file.close()

        stdout_lock.acquire()
        print("\n".join(debug_output))
        stdout_lock.release()


def process_txtrib_mp(txtrib_files, num_processes, output_dir, use_db):
    txtrib_queue = multiprocessing.Queue()
    for txtrib_file in txtrib_files:
        txtrib_queue.put_nowait(os.path.abspath(txtrib_file))

    manager = multiprocessing.Manager()
    peermap = manager.dict()
    peermap_lock = multiprocessing.Lock()
    stdout_lock = multiprocessing.Lock()

    workers = [
        multiprocessing.Process(
            target=process_txtrib_worker, args=(txtrib_queue, peermap,
            peermap_lock, stdout_lock, use_db))
        for _ in xrange(num_processes)]
    for process in workers:
        process.start()
    for process in workers:
        process.join()

    if peermap:
        peer_file = open(os.path.join(output_dir, 'peers.csv'), 'w')
        peer_file.write(
            "# generated on {0} UTC\n".format(datetime.datetime.utcnow()))
        peer_file.write("# peer_id,peer_as,peer_ip\n")
        for peer in peermap.keys():
            peer_id = peermap[peer]
            peer_file.write("{0},{1[0]},{1[1]}\n".format(peer_id, peer))
        peer_file.close()

    # get list of filenames
    # sort by (apparent) date in filename
    # divide by number of workers and insert in lists accordingly
    # if db:
    #   maintain a Manager that keeps the master peer-int mapping dict, which is
    #   cached in each process instance
    # RUN SUBPROCESSES
    # if db:
    #   output peers.csv (peer-int mapping dict)
    #   stitch together prefix_history pickles, and output prefix_origins.csv
    # --------------------
    # FOR EACH process:
    #     output normrib
    #     if not db:
    #       output peers
    #       output ppapp
    #     else:
    #       maintain local cached copy of peer-int mapping dict
    #       output peer_summaries.csv
    #           >> be sure to keep notes of every "as-set originated route"
    #       output prefix_history pickle
    #           >> include start and end of date range for stitching purposes
    #           >> think about what to do about

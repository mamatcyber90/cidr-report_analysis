#!/usr/bin/env python2.6

def normalize_as_path(path):
    # remove AS_SETs and convert to ints
    norm_path = [extract_asn(e) for e in path]
    # remove private ASNs
    norm_path = [e for e in norm_path if e < 64512 or e > 65551]
    norm_path = deprepend_as_path(norm_path)
    # TODO how to deal with 0s in AS_PATH?
    norm_path = deloop_as_path(norm_path)
    return norm_path


def extract_asn(s):
    """A helper function to extract AS numbers from AS_PATH string elements that
    may contain AS_SETs (denoted by '{ASN1, ASN2, ...}'). If the string does not
    contain an AS_SET or if the AS_SET contains only one AS number, the AS
    number is returned. If the AS_SET contains two or more ASNs, 0 is returned.
    If an unparsable AS_PATH element is passed, a ValueError will be raised.

    """
    try:
        s = s.strip()
    except AttributeError:
#        raise TypeError
        # instead of TypeError, try and convert -- it may already be an int
        return int(s)

    if -1 < s.find('{') < s.find('}'):
        if s.find('{') == 0 and s.find('}') == len(s)-1:
            components = s[1:-1].split(',')
            if len(components) == 1:
                return int(components[0].strip())
            else:
                return 0
        else:
            raise ValueError
    else:
        return int(s)


def deprepend_as_path(path):
    """A helper function to remove AS_PATH prepending while maintaining the
    order of AS_PATH traversal. This is used to produce a canonical
    representation of each AS_PATH from a routing policy perspective (but not
    TE, BGP decision algorithm, etc. perspective)

    Example:

        deprepend_as_path([1, 1, 2, 3, 3, 4, 5, 5]) = [1, 2, 3, 4, 5]
        deprepend_as_path([1, 1, 2, 3, 3, 1, 1, 1, 2, 1]) = [1, 2, 3, 1, 2, 1]

    """
    current_as = None
    new_path = []
    for asn in path:
        if asn != current_as:
            current_as = asn
            new_path.append(asn)
    return new_path


def deloop_as_path(path):
    """A helper function to remove AS loops in the AS_PATH while maintaining the
    order of AS number traversal along the path.  This is used in part to
    produce a canonical representation of each AS_PATH from a routing policy
    perspective.

    This function borrows it's logic from CAIDA's straightenRV tool.

    Returns a loop-free version of the original path, or None if the loop cannot
    be resolved (multiple/overlapping loops).
    """
    asn_first_index = {}
    loop_start_index = None
    loop_end_index = None
    for i in xrange(len(path)):
        asn = path[i]
        if asn in asn_first_index:
            if not loop_start_index:
                loop_start_index = asn_first_index[asn]
            loop_end_index = i
        else:
            asn_first_index[asn] = i

    if loop_start_index:
        if path[loop_start_index] == path[loop_end_index]:
            # single loop detected -- replace loop with first AS in loop
            return path[:loop_start_index+1] + path[loop_end_index+1:]
        else:
            # multiple/overlapping loops
            return None
    else:
        return path
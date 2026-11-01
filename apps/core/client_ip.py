"""The one place this app learns *who actually called*.

Deployment shape (doc 09 §2): the machine's nginx is the only public listener and
this container is reachable from it over loopback. So ``request.META['REMOTE_ADDR']``
is the **proxy**, not the resident — useless for the audit trail, and dangerous for
the OTP abuse controls (doc 02 gap #15 throttles per IP). The real client address
arrives in ``X-Forwarded-For``, and the only honest way to read it is through this
helper.

Trust rule: ``X-Forwarded-For`` is a client-writable header on any machine that is
*not* behind a trusted edge, so it is consulted only when ``settings.PROXY_HOPS`` is
non-zero — which is true only with ``BEHIND_PROXY=True``. The edge is configured to
**overwrite** the header (``proxy_set_header X-Forwarded-For $remote_addr`` in
``deploy/nginx/snippets/proxy.conf``), which discards anything a caller injected
before it. A naive ``request.META['HTTP_X_FORWARDED_FOR'].split(',')[0]`` — the
snippet found in most tutorials — takes the *left-most* entry, i.e. exactly the
value an attacker controls.

``PROXY_HOPS`` counts trusted proxy addresses sitting to the right of the real
client, so the answer is ``hops[-PROXY_HOPS]``::

    topology                              X-Forwarded-For        PROXY_HOPS  client
    dev, no edge                          (ignored)              0           REMOTE_ADDR
    one edge that overwrites (ours)       "<client>"             1           hops[-1]
    CDN -> edge, both append              "<client>,<cdn>"       2           hops[-2]
"""

from django.conf import settings


def get_client_ip(request):
    """Return the caller's IP address, or ``''`` when it cannot be determined.

    Never raises and never returns a forged value: with no trusted proxy the answer
    is the socket peer (``REMOTE_ADDR``), any ``X-Forwarded-For`` present is ignored.
    """
    hops = getattr(settings, 'PROXY_HOPS', 0)
    if hops > 0:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        addresses = [entry.strip() for entry in forwarded.split(',') if entry.strip()]
        if addresses:
            # -1 == the address the outermost trusted proxy recorded; walk left
            # once per additional trusted hop. Fewer addresses than hops means
            # something upstream mis-set the count — fall back to the left-most,
            # which is at least a real address rather than an index error.
            return addresses[-hops] if len(addresses) >= hops else addresses[0]
    return request.META.get('REMOTE_ADDR', '')


def is_direct_socket_peer(request):
    """True when ``REMOTE_ADDR`` is the whole story (dev, or a loopback probe).

    Useful for the health endpoint and for logging, so an operator can tell whether
    the edge is wired the way the settings claim it is.
    """
    return getattr(settings, 'PROXY_HOPS', 0) == 0

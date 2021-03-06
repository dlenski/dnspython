# Copyright (C) Dnspython Contributors, see LICENSE for text of ISC license

# Copyright (C) 2001-2017 Nominum, Inc.
#
# Permission to use, copy, modify, and distribute this software and its
# documentation for any purpose with or without fee is hereby granted,
# provided that the above copyright notice and this permission notice
# appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND NOMINUM DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL NOMINUM BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
# OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

"""DNS rdata."""

from importlib import import_module
from io import BytesIO
import base64
import binascii
import io
import inspect

import dns.exception
import dns.name
import dns.rdataclass
import dns.rdatatype
import dns.tokenizer
import dns.wiredata

_hex_chunksize = 32


def _hexify(data, chunksize=_hex_chunksize):
    """Convert a binary string into its hex encoding, broken up into chunks
    of chunksize characters separated by a space.
    """

    line = binascii.hexlify(data)
    return b' '.join([line[i:i + chunksize]
                      for i
                      in range(0, len(line), chunksize)]).decode()

_base64_chunksize = 32


def _base64ify(data, chunksize=_base64_chunksize):
    """Convert a binary string into its base64 encoding, broken up into chunks
    of chunksize characters separated by a space.
    """

    line = base64.b64encode(data)
    return b' '.join([line[i:i + chunksize]
                      for i
                      in range(0, len(line), chunksize)]).decode()

__escaped = b'"\\'

def _escapify(qstring):
    """Escape the characters in a quoted string which need it."""

    if isinstance(qstring, str):
        qstring = qstring.encode()
    if not isinstance(qstring, bytearray):
        qstring = bytearray(qstring)

    text = ''
    for c in qstring:
        if c in __escaped:
            text += '\\' + chr(c)
        elif c >= 0x20 and c < 0x7F:
            text += chr(c)
        else:
            text += '\\%03d' % c
    return text


def _truncate_bitmap(what):
    """Determine the index of greatest byte that isn't all zeros, and
    return the bitmap that contains all the bytes less than that index.
    """

    for i in range(len(what) - 1, -1, -1):
        if what[i] != 0:
            return what[0: i + 1]
    return what[0:1]

def _constify(o):
    """
    Convert mutable types to immutable types.
    """
    if isinstance(o, bytearray):
        return bytes(o)
    if isinstance(o, tuple):
        try:
            hash(o)
            return o
        except Exception:
            return tuple(_constify(elt) for elt in o)
    if isinstance(o, list):
        return tuple(_constify(elt) for elt in o)
    return o

class Rdata(object):
    """Base class for all DNS rdata types."""

    __slots__ = ['rdclass', 'rdtype']

    def __init__(self, rdclass, rdtype):
        """Initialize an rdata.

        *rdclass*, an ``int`` is the rdataclass of the Rdata.
        *rdtype*, an ``int`` is the rdatatype of the Rdata.
        """

        object.__setattr__(self, 'rdclass', rdclass)
        object.__setattr__(self, 'rdtype', rdtype)

    def __setattr__(self, name, value):
        # Rdatas are immutable
        raise TypeError("object doesn't support attribute assignment")

    def __delattr__(self, name):
        # Rdatas are immutable
        raise TypeError("object doesn't support attribute deletion")

    def covers(self):
        """Return the type a Rdata covers.

        DNS SIG/RRSIG rdatas apply to a specific type; this type is
        returned by the covers() function.  If the rdata type is not
        SIG or RRSIG, dns.rdatatype.NONE is returned.  This is useful when
        creating rdatasets, allowing the rdataset to contain only RRSIGs
        of a particular type, e.g. RRSIG(NS).

        Returns an ``int``.
        """

        return dns.rdatatype.NONE

    def extended_rdatatype(self):
        """Return a 32-bit type value, the least significant 16 bits of
        which are the ordinary DNS type, and the upper 16 bits of which are
        the "covered" type, if any.

        Returns an ``int``.
        """

        return self.covers() << 16 | self.rdtype

    def to_text(self, origin=None, relativize=True, **kw):
        """Convert an rdata to text format.

        Returns a ``text``.
        """

        raise NotImplementedError

    def to_wire(self, file, compress=None, origin=None):
        """Convert an rdata to wire format.

        Returns ``None``.
        """

        raise NotImplementedError

    def to_generic(self, origin=None):
        """Creates a dns.rdata.GenericRdata equivalent of this rdata.

        Returns a ``dns.rdata.GenericRdata``.
        """
        f = io.BytesIO()
        self.to_wire(f, origin=origin)
        return dns.rdata.GenericRdata(self.rdclass, self.rdtype, f.getvalue())

    def to_digestable(self, origin=None):
        """Convert rdata to a format suitable for digesting in hashes.  This
        is also the DNSSEC canonical form.

        Returns a ``binary``.
        """

        f = BytesIO()
        self.to_wire(f, None, origin)
        return f.getvalue()

    def validate(self):
        """Check that the current contents of the rdata's fields are
        valid.

        If you change an rdata by assigning to its fields,
        it is a good idea to call validate() when you are done making
        changes.

        Raises various exceptions if there are problems.

        Returns ``None``.
        """

        dns.rdata.from_text(self.rdclass, self.rdtype, self.to_text())

    def __repr__(self):
        covers = self.covers()
        if covers == dns.rdatatype.NONE:
            ctext = ''
        else:
            ctext = '(' + dns.rdatatype.to_text(covers) + ')'
        return '<DNS ' + dns.rdataclass.to_text(self.rdclass) + ' ' + \
               dns.rdatatype.to_text(self.rdtype) + ctext + ' rdata: ' + \
               str(self) + '>'

    def __str__(self):
        return self.to_text()

    def _cmp(self, other):
        """Compare an rdata with another rdata of the same rdtype and
        rdclass.

        Return < 0 if self < other in the DNSSEC ordering, 0 if self
        == other, and > 0 if self > other.

        """
        our = self.to_digestable(dns.name.root)
        their = other.to_digestable(dns.name.root)
        if our == their:
            return 0
        elif our > their:
            return 1
        else:
            return -1

    def __eq__(self, other):
        if not isinstance(other, Rdata):
            return False
        if self.rdclass != other.rdclass or self.rdtype != other.rdtype:
            return False
        return self._cmp(other) == 0

    def __ne__(self, other):
        if not isinstance(other, Rdata):
            return True
        if self.rdclass != other.rdclass or self.rdtype != other.rdtype:
            return True
        return self._cmp(other) != 0

    def __lt__(self, other):
        if not isinstance(other, Rdata) or \
                self.rdclass != other.rdclass or self.rdtype != other.rdtype:

            return NotImplemented
        return self._cmp(other) < 0

    def __le__(self, other):
        if not isinstance(other, Rdata) or \
                self.rdclass != other.rdclass or self.rdtype != other.rdtype:
            return NotImplemented
        return self._cmp(other) <= 0

    def __ge__(self, other):
        if not isinstance(other, Rdata) or \
                self.rdclass != other.rdclass or self.rdtype != other.rdtype:
            return NotImplemented
        return self._cmp(other) >= 0

    def __gt__(self, other):
        if not isinstance(other, Rdata) or \
                self.rdclass != other.rdclass or self.rdtype != other.rdtype:
            return NotImplemented
        return self._cmp(other) > 0

    def __hash__(self):
        return hash(self.to_digestable(dns.name.root))

    @classmethod
    def from_text(cls, rdclass, rdtype, tok, origin=None, relativize=True,
                  relativize_to=None):
        raise NotImplementedError

    @classmethod
    def from_wire(cls, rdclass, rdtype, wire, current, rdlen, origin=None):
        raise NotImplementedError

    def replace(self, **kwargs):
        """
        Create a new Rdata instance based on the instance replace was
        invoked on. It is possible to pass different parameters to
        override the corresponding properties of the base Rdata.

        Any field specific to the Rdata type can be replaced, but the
        *rdtype* and *rdclass* fields cannot.

        Returns an instance of the same Rdata subclass as *self*.
        """

        # Get the constructor parameters.
        parameters = inspect.signature(self.__init__).parameters

        # Ensure that all of the arguments correspond to valid fields.
        # Don't allow rdclass or rdtype to be changed, though.
        for key in kwargs:
            if key not in parameters:
                raise AttributeError("'{}' object has no attribute '{}'"
                                     .format(self.__class__.__name__, key))
            if key in ('rdclass', 'rdtype'):
                raise AttributeError("Cannot overwrite '{}' attribute '{}'"
                                     .format(self.__class__.__name__, key))

        # Construct the parameter list.  For each field, use the value in
        # kwargs if present, and the current value otherwise.
        args = (kwargs.get(key, getattr(self, key)) for key in parameters)

        # Create and return the new object.
        return self.__class__(*args)


class GenericRdata(Rdata):

    """Generic Rdata Class

    This class is used for rdata types for which we have no better
    implementation.  It implements the DNS "unknown RRs" scheme.
    """

    __slots__ = ['data']

    def __init__(self, rdclass, rdtype, data):
        super(GenericRdata, self).__init__(rdclass, rdtype)
        object.__setattr__(self, 'data', data)

    def to_text(self, origin=None, relativize=True, **kw):
        return r'\# %d ' % len(self.data) + _hexify(self.data)

    @classmethod
    def from_text(cls, rdclass, rdtype, tok, origin=None, relativize=True,
                  relativize_to=None):
        token = tok.get()
        if not token.is_identifier() or token.value != r'\#':
            raise dns.exception.SyntaxError(
                r'generic rdata does not start with \#')
        length = tok.get_int()
        chunks = []
        while 1:
            token = tok.get()
            if token.is_eol_or_eof():
                break
            chunks.append(token.value.encode())
        hex = b''.join(chunks)
        data = binascii.unhexlify(hex)
        if len(data) != length:
            raise dns.exception.SyntaxError(
                'generic rdata hex data has wrong length')
        return cls(rdclass, rdtype, data)

    def to_wire(self, file, compress=None, origin=None):
        file.write(self.data)

    @classmethod
    def from_wire(cls, rdclass, rdtype, wire, current, rdlen, origin=None):
        return cls(rdclass, rdtype, wire[current: current + rdlen])

_rdata_classes = {}
_module_prefix = 'dns.rdtypes'

def get_rdata_class(rdclass, rdtype):
    cls = _rdata_classes.get((rdclass, rdtype))
    if not cls:
        cls = _rdata_classes.get((dns.rdatatype.ANY, rdtype))
        if not cls:
            rdclass_text = dns.rdataclass.to_text(rdclass)
            rdtype_text = dns.rdatatype.to_text(rdtype)
            rdtype_text = rdtype_text.replace('-', '_')
            try:
                mod = import_module('.'.join([_module_prefix,
                                              rdclass_text, rdtype_text]))
                cls = getattr(mod, rdtype_text)
                _rdata_classes[(rdclass, rdtype)] = cls
            except ImportError:
                try:
                    mod = import_module('.'.join([_module_prefix,
                                                  'ANY', rdtype_text]))
                    cls = getattr(mod, rdtype_text)
                    _rdata_classes[(dns.rdataclass.ANY, rdtype)] = cls
                    _rdata_classes[(rdclass, rdtype)] = cls
                except ImportError:
                    pass
    if not cls:
        cls = GenericRdata
        _rdata_classes[(rdclass, rdtype)] = cls
    return cls


def from_text(rdclass, rdtype, tok, origin=None, relativize=True,
              relativize_to=None):
    """Build an rdata object from text format.

    This function attempts to dynamically load a class which
    implements the specified rdata class and type.  If there is no
    class-and-type-specific implementation, the GenericRdata class
    is used.

    Once a class is chosen, its from_text() class method is called
    with the parameters to this function.

    If *tok* is a ``text``, then a tokenizer is created and the string
    is used as its input.

    *rdclass*, an ``int``, the rdataclass.

    *rdtype*, an ``int``, the rdatatype.

    *tok*, a ``dns.tokenizer.Tokenizer`` or a ``text``.

    *origin*, a ``dns.name.Name`` (or ``None``), the
    origin to use for relative names.

    *relativize*, a ``bool``.  If true, name will be relativized.

    *relativize_to*, a ``dns.name.Name`` (or ``None``), the origin to use
    when relativizing names.  If not set, the *origin* value will be used.

    Returns an instance of the chosen Rdata subclass.
    """

    if isinstance(tok, str):
        tok = dns.tokenizer.Tokenizer(tok)
    cls = get_rdata_class(rdclass, rdtype)
    if cls != GenericRdata:
        # peek at first token
        token = tok.get()
        tok.unget(token)
        if token.is_identifier() and \
           token.value == r'\#':
            #
            # Known type using the generic syntax.  Extract the
            # wire form from the generic syntax, and then run
            # from_wire on it.
            #
            rdata = GenericRdata.from_text(rdclass, rdtype, tok, origin,
                                           relativize, relativize_to)
            return from_wire(rdclass, rdtype, rdata.data, 0, len(rdata.data),
                             origin)
    return cls.from_text(rdclass, rdtype, tok, origin, relativize,
                         relativize_to)


def from_wire(rdclass, rdtype, wire, current, rdlen, origin=None):
    """Build an rdata object from wire format

    This function attempts to dynamically load a class which
    implements the specified rdata class and type.  If there is no
    class-and-type-specific implementation, the GenericRdata class
    is used.

    Once a class is chosen, its from_wire() class method is called
    with the parameters to this function.

    *rdclass*, an ``int``, the rdataclass.

    *rdtype*, an ``int``, the rdatatype.

    *wire*, a ``binary``, the wire-format message.

    *current*, an ``int``, the offset in wire of the beginning of
    the rdata.

    *rdlen*, an ``int``, the length of the wire-format rdata

    *origin*, a ``dns.name.Name`` (or ``None``).  If not ``None``,
    then names will be relativized to this origin.

    Returns an instance of the chosen Rdata subclass.
    """

    wire = dns.wiredata.maybe_wrap(wire)
    cls = get_rdata_class(rdclass, rdtype)
    return cls.from_wire(rdclass, rdtype, wire, current, rdlen, origin)


class RdatatypeExists(dns.exception.DNSException):
    """DNS rdatatype already exists."""
    supp_kwargs = {'rdclass', 'rdtype'}
    fmt = "The rdata type with class {rdclass} and rdtype {rdtype} " + \
        "already exists."


def register_type(implementation, rdtype, rdtype_text, is_singleton=False,
                  rdclass=dns.rdataclass.IN):
    """Dynamically register a module to handle an rdatatype.

    *implementation*, a module implementing the type in the usual dnspython
    way.

    *rdtype*, an ``int``, the rdatatype to register.

    *rdtype_text*, a ``text``, the textual form of the rdatatype.

    *is_singleton*, a ``bool``, indicating if the type is a singleton (i.e.
    RRsets of the type can have only one member.)

    *rdclass*, the rdataclass of the type, or ``dns.rdataclass.ANY`` if
    it applies to all classes.
    """

    existing_cls = get_rdata_class(rdclass, rdtype)
    if existing_cls != GenericRdata:
        raise RdatatypeExists(rdclass=rdclass, rdtype=rdtype)
    _rdata_classes[(rdclass, rdtype)] = getattr(implementation,
                                                rdtype_text.replace('-', '_'))
    dns.rdatatype.register_type(rdtype, rdtype_text, is_singleton)

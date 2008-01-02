# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php

"""Defines operators used in SQL expressions."""

from operator import and_, or_, inv, add, mul, sub, div, mod, truediv, \
     lt, le, ne, gt, ge, eq

from sqlalchemy.util import Set

def from_():
    raise NotImplementedError()

def as_():
    raise NotImplementedError()

def exists():
    raise NotImplementedError()

def is_():
    raise NotImplementedError()

def isnot():
    raise NotImplementedError()

def op(a, opstring, b):
    return a.op(opstring)(b)

def like_op(a, b):
    return a.like(b)

def notlike_op(a, b):
    raise NotImplementedError()

def ilike_op(a, b):
    return a.ilike(b)

def notilike_op(a, b):
    raise NotImplementedError()

def between_op(a, b, c):
    return a.between(b, c)

def in_op(a, b):
    return a.in_(*b)

def notin_op(a, b):
    raise NotImplementedError()

def distinct_op(a):
    return a.distinct()

def startswith_op(a, b):
    return a.startswith(b)

def endswith_op(a, b):
    return a.endswith(b)

def contains_op(a, b):
    return a.contains(b)

def comma_op(a, b):
    raise NotImplementedError()

def concat_op(a, b):
    return a.concat(b)

def desc_op(a):
    return a.desc()

def asc_op(a):
    return a.asc()

_commutative = Set([eq, ne, add, mul])
def is_commutative(op):
    return op in _commutative
    
_smallest = object()
_largest = object()

_PRECEDENCE = {
    from_:15,
    mul:7,
    div:7,
    mod:7,
    add:6,
    sub:6,
    concat_op:6,
    ilike_op:5,
    notilike_op:5,
    like_op:5,
    notlike_op:5,
    in_op:5,
    notin_op:5,
    is_:5,
    isnot:5,
    eq:5,
    ne:5,
    gt:5,
    lt:5,
    ge:5,
    le:5,
    between_op:5,
    distinct_op:5,
    inv:5,
    and_:3,
    or_:2,
    comma_op:-1,
    as_:-1,
    exists:0,
    _smallest: -1000,
    _largest: 1000
}

def is_precedent(operator, against):
    return _PRECEDENCE.get(operator, _PRECEDENCE[_smallest]) <= _PRECEDENCE.get(against, _PRECEDENCE[_largest])

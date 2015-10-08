# util/langhelpers.py
# Copyright (C) 2005-2011 the SQLAlchemy authors and contributors <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php

"""Routines to help with the creation, loading and introspection of
modules, classes, hierarchies, attributes, functions, and methods.

"""
import itertools
import inspect
import operator
import sys
import types
import warnings
from compat import update_wrapper, set_types, threading
from sqlalchemy import exc

def _unique_symbols(used, *bases):
    used = set(used)
    for base in bases:
        pool = itertools.chain((base,),
                               itertools.imap(lambda i: base + str(i),
                                              xrange(1000)))
        for sym in pool:
            if sym not in used:
                used.add(sym)
                yield sym
                break
        else:
            raise NameError("exhausted namespace for symbol base %s" % base)

def decorator(target):
    """A signature-matching decorator factory."""

    def decorate(fn):
        if not inspect.isfunction(fn):
            raise Exception("not a decoratable function")
        spec = inspect.getargspec(fn)
        names = tuple(spec[0]) + spec[1:3] + (fn.func_name,)
        targ_name, fn_name = _unique_symbols(names, 'target', 'fn')

        metadata = dict(target=targ_name, fn=fn_name)
        metadata.update(format_argspec_plus(spec, grouped=False))

        code = 'lambda %(args)s: %(target)s(%(fn)s, %(apply_kw)s)' % (
                metadata)
        decorated = eval(code, {targ_name:target, fn_name:fn})
        decorated.func_defaults = getattr(fn, 'im_func', fn).func_defaults
        return update_wrapper(decorated, fn)
    return update_wrapper(decorate, target)



def get_cls_kwargs(cls):
    """Return the full set of inherited kwargs for the given `cls`.

    Probes a class's __init__ method, collecting all named arguments.  If the
    __init__ defines a \**kwargs catch-all, then the constructor is presumed to
    pass along unrecognized keywords to it's base classes, and the collection
    process is repeated recursively on each of the bases.

    Uses a subset of inspect.getargspec() to cut down on method overhead.
    No anonymous tuple arguments please !

    """

    for c in cls.__mro__:
        if '__init__' in c.__dict__:
            stack = set([c])
            break
    else:
        return []

    args = set()
    while stack:
        class_ = stack.pop()
        ctr = class_.__dict__.get('__init__', False)
        if (not ctr or
            not isinstance(ctr, types.FunctionType) or
            not isinstance(ctr.func_code, types.CodeType)):
            stack.update(class_.__bases__)
            continue

        # this is shorthand for
        # names, _, has_kw, _ = inspect.getargspec(ctr)

        names, has_kw = inspect_func_args(ctr)
        args.update(names)
        if has_kw:
            stack.update(class_.__bases__)
    args.discard('self')
    return args

try:
    from inspect import CO_VARKEYWORDS
    def inspect_func_args(fn):
        co = fn.func_code
        nargs = co.co_argcount
        names = co.co_varnames
        args = list(names[:nargs])
        has_kw = bool(co.co_flags & CO_VARKEYWORDS)
        return args, has_kw
except ImportError:
    def inspect_func_args(fn):
        names, _, has_kw, _ = inspect.getargspec(fn)
        return names, bool(has_kw)

def get_func_kwargs(func):
    """Return the set of legal kwargs for the given `func`.

    Uses getargspec so is safe to call for methods, functions,
    etc.

    """

    return inspect.getargspec(func)[0]

def format_argspec_plus(fn, grouped=True):
    """Returns a dictionary of formatted, introspected function arguments.

    A enhanced variant of inspect.formatargspec to support code generation.

    fn
       An inspectable callable or tuple of inspect getargspec() results.
    grouped
      Defaults to True; include (parens, around, argument) lists

    Returns:

    args
      Full inspect.formatargspec for fn
    self_arg
      The name of the first positional argument, varargs[0], or None
      if the function defines no positional arguments.
    apply_pos
      args, re-written in calling rather than receiving syntax.  Arguments are
      passed positionally.
    apply_kw
      Like apply_pos, except keyword-ish args are passed as keywords.

    Example::

      >>> format_argspec_plus(lambda self, a, b, c=3, **d: 123)
      {'args': '(self, a, b, c=3, **d)',
       'self_arg': 'self',
       'apply_kw': '(self, a, b, c=c, **d)',
       'apply_pos': '(self, a, b, c, **d)'}

    """
    spec = callable(fn) and inspect.getargspec(fn) or fn
    args = inspect.formatargspec(*spec)
    if spec[0]:
        self_arg = spec[0][0]
    elif spec[1]:
        self_arg = '%s[0]' % spec[1]
    else:
        self_arg = None
    apply_pos = inspect.formatargspec(spec[0], spec[1], spec[2])
    defaulted_vals = spec[3] is not None and spec[0][0-len(spec[3]):] or ()
    apply_kw = inspect.formatargspec(spec[0], spec[1], spec[2], defaulted_vals,
                                     formatvalue=lambda x: '=' + x)
    if grouped:
        return dict(args=args, self_arg=self_arg,
                    apply_pos=apply_pos, apply_kw=apply_kw)
    else:
        return dict(args=args[1:-1], self_arg=self_arg,
                    apply_pos=apply_pos[1:-1], apply_kw=apply_kw[1:-1])

def format_argspec_init(method, grouped=True):
    """format_argspec_plus with considerations for typical __init__ methods

    Wraps format_argspec_plus with error handling strategies for typical
    __init__ cases::

      object.__init__ -> (self)
      other unreflectable (usually C) -> (self, *args, **kwargs)

    """
    try:
        return format_argspec_plus(method, grouped=grouped)
    except TypeError:
        self_arg = 'self'
        if method is object.__init__:
            args = grouped and '(self)' or 'self'
        else:
            args = (grouped and '(self, *args, **kwargs)'
                            or 'self, *args, **kwargs')
        return dict(self_arg='self', args=args, apply_pos=args, apply_kw=args)

def getargspec_init(method):
    """inspect.getargspec with considerations for typical __init__ methods

    Wraps inspect.getargspec with error handling for typical __init__ cases::

      object.__init__ -> (self)
      other unreflectable (usually C) -> (self, *args, **kwargs)

    """
    try:
        return inspect.getargspec(method)
    except TypeError:
        if method is object.__init__:
            return (['self'], None, None, None)
        else:
            return (['self'], 'args', 'kwargs', None)


def unbound_method_to_callable(func_or_cls):
    """Adjust the incoming callable such that a 'self' argument is not required."""

    if isinstance(func_or_cls, types.MethodType) and not func_or_cls.im_self:
        return func_or_cls.im_func
    else:
        return func_or_cls

class portable_instancemethod(object):
    """Turn an instancemethod into a (parent, name) pair
    to produce a serializable callable.

    """
    def __init__(self, meth):
        self.target = meth.im_self
        self.name = meth.__name__

    def __call__(self, *arg, **kw):
        return getattr(self.target, self.name)(*arg, **kw)

def class_hierarchy(cls):
    """Return an unordered sequence of all classes related to cls.

    Traverses diamond hierarchies.

    Fibs slightly: subclasses of builtin types are not returned.  Thus
    class_hierarchy(class A(object)) returns (A, object), not A plus every
    class systemwide that derives from object.

    Old-style classes are discarded and hierarchies rooted on them
    will not be descended.

    """
    # Py2K
    if isinstance(cls, types.ClassType):
        return list()
    # end Py2K
    hier = set([cls])
    process = list(cls.__mro__)
    while process:
        c = process.pop()
        # Py2K
        if isinstance(c, types.ClassType):
            continue
        for b in (_ for _ in c.__bases__
                  if _ not in hier and not isinstance(_, types.ClassType)):
        # end Py2K
        # Py3K
        #for b in (_ for _ in c.__bases__
        #          if _ not in hier):
            process.append(b)
            hier.add(b)
        # Py3K
        #if c.__module__ == 'builtins' or not hasattr(c, '__subclasses__'):
        #    continue
        # Py2K
        if c.__module__ == '__builtin__' or not hasattr(c, '__subclasses__'):
            continue
        # end Py2K
        for s in [_ for _ in c.__subclasses__() if _ not in hier]:
            process.append(s)
            hier.add(s)
    return list(hier)

def iterate_attributes(cls):
    """iterate all the keys and attributes associated
       with a class, without using getattr().

       Does not use getattr() so that class-sensitive
       descriptors (i.e. property.__get__()) are not called.

    """
    keys = dir(cls)
    for key in keys:
        for c in cls.__mro__:
            if key in c.__dict__:
                yield (key, c.__dict__[key])
                break

def monkeypatch_proxied_specials(into_cls, from_cls, skip=None, only=None,
                                 name='self.proxy', from_instance=None):
    """Automates delegation of __specials__ for a proxying type."""

    if only:
        dunders = only
    else:
        if skip is None:
            skip = ('__slots__', '__del__', '__getattribute__',
                    '__metaclass__', '__getstate__', '__setstate__')
        dunders = [m for m in dir(from_cls)
                   if (m.startswith('__') and m.endswith('__') and
                       not hasattr(into_cls, m) and m not in skip)]
    for method in dunders:
        try:
            fn = getattr(from_cls, method)
            if not hasattr(fn, '__call__'):
                continue
            fn = getattr(fn, 'im_func', fn)
        except AttributeError:
            continue
        try:
            spec = inspect.getargspec(fn)
            fn_args = inspect.formatargspec(spec[0])
            d_args = inspect.formatargspec(spec[0][1:])
        except TypeError:
            fn_args = '(self, *args, **kw)'
            d_args = '(*args, **kw)'

        py = ("def %(method)s%(fn_args)s: "
              "return %(name)s.%(method)s%(d_args)s" % locals())

        env = from_instance is not None and {name: from_instance} or {}
        exec py in env
        try:
            env[method].func_defaults = fn.func_defaults
        except AttributeError:
            pass
        setattr(into_cls, method, env[method])


def methods_equivalent(meth1, meth2):
    """Return True if the two methods are the same implementation."""

    # Py3K
    #return getattr(meth1, '__func__', meth1) is getattr(meth2, '__func__', meth2)
    # Py2K
    return getattr(meth1, 'im_func', meth1) is getattr(meth2, 'im_func', meth2)
    # end Py2K

def as_interface(obj, cls=None, methods=None, required=None):
    """Ensure basic interface compliance for an instance or dict of callables.

    Checks that ``obj`` implements public methods of ``cls`` or has members
    listed in ``methods``. If ``required`` is not supplied, implementing at
    least one interface method is sufficient. Methods present on ``obj`` that
    are not in the interface are ignored.

    If ``obj`` is a dict and ``dict`` does not meet the interface
    requirements, the keys of the dictionary are inspected. Keys present in
    ``obj`` that are not in the interface will raise TypeErrors.

    Raises TypeError if ``obj`` does not meet the interface criteria.

    In all passing cases, an object with callable members is returned.  In the
    simple case, ``obj`` is returned as-is; if dict processing kicks in then
    an anonymous class is returned.

    obj
      A type, instance, or dictionary of callables.
    cls
      Optional, a type.  All public methods of cls are considered the
      interface.  An ``obj`` instance of cls will always pass, ignoring
      ``required``..
    methods
      Optional, a sequence of method names to consider as the interface.
    required
      Optional, a sequence of mandatory implementations. If omitted, an
      ``obj`` that provides at least one interface method is considered
      sufficient.  As a convenience, required may be a type, in which case
      all public methods of the type are required.

    """
    if not cls and not methods:
        raise TypeError('a class or collection of method names are required')

    if isinstance(cls, type) and isinstance(obj, cls):
        return obj

    interface = set(methods or [m for m in dir(cls) if not m.startswith('_')])
    implemented = set(dir(obj))

    complies = operator.ge
    if isinstance(required, type):
        required = interface
    elif not required:
        required = set()
        complies = operator.gt
    else:
        required = set(required)

    if complies(implemented.intersection(interface), required):
        return obj

    # No dict duck typing here.
    if not type(obj) is dict:
        qualifier = complies is operator.gt and 'any of' or 'all of'
        raise TypeError("%r does not implement %s: %s" % (
            obj, qualifier, ', '.join(interface)))

    class AnonymousInterface(object):
        """A callable-holding shell."""

    if cls:
        AnonymousInterface.__name__ = 'Anonymous' + cls.__name__
    found = set()

    for method, impl in dictlike_iteritems(obj):
        if method not in interface:
            raise TypeError("%r: unknown in this interface" % method)
        if not callable(impl):
            raise TypeError("%r=%r is not callable" % (method, impl))
        setattr(AnonymousInterface, method, staticmethod(impl))
        found.add(method)

    if complies(found, required):
        return AnonymousInterface

    raise TypeError("dictionary does not contain required keys %s" %
                    ', '.join(required - found))


class memoized_property(object):
    """A read-only @property that is only evaluated once."""
    def __init__(self, fget, doc=None):
        self.fget = fget
        self.__doc__ = doc or fget.__doc__
        self.__name__ = fget.__name__

    def __get__(self, obj, cls):
        if obj is None:
            return self
        obj.__dict__[self.__name__] = result = self.fget(obj)
        return result


class memoized_instancemethod(object):
    """Decorate a method memoize its return value.

    Best applied to no-arg methods: memoization is not sensitive to
    argument values, and will always return the same value even when
    called with different arguments.

    """
    def __init__(self, fget, doc=None):
        self.fget = fget
        self.__doc__ = doc or fget.__doc__
        self.__name__ = fget.__name__

    def __get__(self, obj, cls):
        if obj is None:
            return self
        def oneshot(*args, **kw):
            result = self.fget(obj, *args, **kw)
            memo = lambda *a, **kw: result
            memo.__name__ = self.__name__
            memo.__doc__ = self.__doc__
            obj.__dict__[self.__name__] = memo
            return result
        oneshot.__name__ = self.__name__
        oneshot.__doc__ = self.__doc__
        return oneshot

def reset_memoized(instance, name):
    instance.__dict__.pop(name, None)


class group_expirable_memoized_property(object):
    """A family of @memoized_properties that can be expired in tandem."""

    def __init__(self):
        self.attributes = []

    def expire_instance(self, instance):
        """Expire all memoized properties for *instance*."""
        stash = instance.__dict__
        for attribute in self.attributes:
            stash.pop(attribute, None)

    def __call__(self, fn):
        self.attributes.append(fn.__name__)
        return memoized_property(fn)

class importlater(object):
    """Deferred import object.

    e.g.::

        somesubmod = importlater("mypackage.somemodule", "somesubmod")

    is equivalent to::

        from mypackage.somemodule import somesubmod

    except evaluted upon attribute access to "somesubmod".

    """
    def __init__(self, path, addtl=None):
        self._il_path = path
        self._il_addtl = addtl

    @memoized_property
    def module(self):
        if self._il_addtl:
            m = __import__(self._il_path, globals(), locals(),
                                [self._il_addtl])
            try:
                return getattr(m, self._il_addtl)
            except AttributeError:
                raise ImportError(
                        "Module %s has no attribute '%s'" %
                        (self._il_path, self._il_addtl)
                    )
        else:
            m = __import__(self._il_path)
            for token in self._il_path.split(".")[1:]:
                m = getattr(m, token)
            return m

    def __getattr__(self, key):
        try:
            attr = getattr(self.module, key)
        except AttributeError:
            raise AttributeError(
                        "Module %s has no attribute '%s'" %
                        (self._il_path, key)
                    )
        self.__dict__[key] = attr
        return attr

# from paste.deploy.converters
def asbool(obj):
    if isinstance(obj, (str, unicode)):
        obj = obj.strip().lower()
        if obj in ['true', 'yes', 'on', 'y', 't', '1']:
            return True
        elif obj in ['false', 'no', 'off', 'n', 'f', '0']:
            return False
        else:
            raise ValueError("String is not true/false: %r" % obj)
    return bool(obj)

def bool_or_str(*text):
    """Return a callable that will evaulate a string as
    boolean, or one of a set of "alternate" string values.

    """
    def bool_or_value(obj):
        if obj in text:
            return obj
        else:
            return asbool(obj)
    return bool_or_value

def coerce_kw_type(kw, key, type_, flexi_bool=True):
    """If 'key' is present in dict 'kw', coerce its value to type 'type\_' if
    necessary.  If 'flexi_bool' is True, the string '0' is considered false
    when coercing to boolean.
    """

    if key in kw and type(kw[key]) is not type_ and kw[key] is not None:
        if type_ is bool and flexi_bool:
            kw[key] = asbool(kw[key])
        else:
            kw[key] = type_(kw[key])


def constructor_copy(obj, cls, **kw):
    """Instantiate cls using the __dict__ of obj as constructor arguments.

    Uses inspect to match the named arguments of ``cls``.

    """

    names = get_cls_kwargs(cls)
    kw.update((k, obj.__dict__[k]) for k in names if k in obj.__dict__)
    return cls(**kw)


def duck_type_collection(specimen, default=None):
    """Given an instance or class, guess if it is or is acting as one of
    the basic collection types: list, set and dict.  If the __emulates__
    property is present, return that preferentially.
    """

    if hasattr(specimen, '__emulates__'):
        # canonicalize set vs sets.Set to a standard: the builtin set
        if (specimen.__emulates__ is not None and
            issubclass(specimen.__emulates__, set_types)):
            return set
        else:
            return specimen.__emulates__

    isa = isinstance(specimen, type) and issubclass or isinstance
    if isa(specimen, list):
        return list
    elif isa(specimen, set_types):
        return set
    elif isa(specimen, dict):
        return dict

    if hasattr(specimen, 'append'):
        return list
    elif hasattr(specimen, 'add'):
        return set
    elif hasattr(specimen, 'set'):
        return dict
    else:
        return default

def assert_arg_type(arg, argtype, name):
    if isinstance(arg, argtype):
        return arg
    else:
        if isinstance(argtype, tuple):
            raise exc.ArgumentError(
                            "Argument '%s' is expected to be one of type %s, got '%s'" %
                            (name, ' or '.join("'%s'" % a for a in argtype), type(arg)))
        else:
            raise exc.ArgumentError(
                            "Argument '%s' is expected to be of type '%s', got '%s'" %
                            (name, argtype, type(arg)))


def dictlike_iteritems(dictlike):
    """Return a (key, value) iterator for almost any dict-like object."""

    # Py3K
    #if hasattr(dictlike, 'items'):
    #    return dictlike.items()
    # Py2K
    if hasattr(dictlike, 'iteritems'):
        return dictlike.iteritems()
    elif hasattr(dictlike, 'items'):
        return iter(dictlike.items())
    # end Py2K

    getter = getattr(dictlike, '__getitem__', getattr(dictlike, 'get', None))
    if getter is None:
        raise TypeError(
            "Object '%r' is not dict-like" % dictlike)

    if hasattr(dictlike, 'iterkeys'):
        def iterator():
            for key in dictlike.iterkeys():
                yield key, getter(key)
        return iterator()
    elif hasattr(dictlike, 'keys'):
        return iter((key, getter(key)) for key in dictlike.keys())
    else:
        raise TypeError(
            "Object '%r' is not dict-like" % dictlike)


class classproperty(property):
    """A decorator that behaves like @property except that operates
    on classes rather than instances.

    The decorator is currently special when using the declarative
    module, but note that the
    :class:`~.sqlalchemy.ext.declarative.declared_attr`
    decorator should be used for this purpose with declarative.

    """

    def __init__(self, fget, *arg, **kw):
        super(classproperty, self).__init__(fget, *arg, **kw)
        self.__doc__ = fget.__doc__

    def __get__(desc, self, cls):
        return desc.fget(cls)


class _symbol(object):
    def __init__(self, name, doc=None):
        """Construct a new named symbol."""
        assert isinstance(name, str)
        self.name = name
        if doc:
            self.__doc__ = doc
    def __reduce__(self):
        return symbol, (self.name,)
    def __repr__(self):
        return "<symbol '%s>" % self.name

_symbol.__name__ = 'symbol'


class symbol(object):
    """A constant symbol.

    >>> symbol('foo') is symbol('foo')
    True
    >>> symbol('foo')
    <symbol 'foo>

    A slight refinement of the MAGICCOOKIE=object() pattern.  The primary
    advantage of symbol() is its repr().  They are also singletons.

    Repeated calls of symbol('name') will all return the same instance.

    The optional ``doc`` argument assigns to ``__doc__``.  This
    is strictly so that Sphinx autoattr picks up the docstring we want
    (it doesn't appear to pick up the in-module docstring if the datamember
    is in a different module - autoattribute also blows up completely).
    If Sphinx fixes/improves this then we would no longer need
    ``doc`` here.

    """
    symbols = {}
    _lock = threading.Lock()

    def __new__(cls, name, doc=None):
        cls._lock.acquire()
        try:
            sym = cls.symbols.get(name)
            if sym is None:
                cls.symbols[name] = sym = _symbol(name, doc)
            return sym
        finally:
            symbol._lock.release()


_creation_order = 1
def set_creation_order(instance):
    """Assign a '_creation_order' sequence to the given instance.

    This allows multiple instances to be sorted in order of creation
    (typically within a single thread; the counter is not particularly
    threadsafe).

    """
    global _creation_order
    instance._creation_order = _creation_order
    _creation_order +=1

def warn_exception(func, *args, **kwargs):
    """executes the given function, catches all exceptions and converts to a warning."""
    try:
        return func(*args, **kwargs)
    except:
        warn("%s('%s') ignored" % sys.exc_info()[0:2])


def warn(msg, stacklevel=3):
    """Issue a warning.

    If msg is a string, :class:`.exc.SAWarning` is used as
    the category.

    .. note:: This function is swapped out when the test suite
       runs, with a compatible version that uses
       warnings.warn_explicit, so that the warnings registry can
       be controlled.

    """
    if isinstance(msg, basestring):
        warnings.warn(msg, exc.SAWarning, stacklevel=stacklevel)
    else:
        warnings.warn(msg, stacklevel=stacklevel)

NoneType = type(None)

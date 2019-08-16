"""
Implementations of standard library functions, because it's not possible to
understand them with Jedi.

To add a new implementation, create a function and add it to the
``_implemented`` dict at the bottom of this module.

Note that this module exists only to implement very specific functionality in
the standard library. The usual way to understand the standard library is the
compiled module that returns the types for C-builtins.
"""
import parso
import os

from jedi._compatibility import force_unicode, Parameter
from jedi import debug
from jedi.inference.utils import safe_property
from jedi.inference.helpers import get_str_or_none
from jedi.inference.arguments import ValuesArguments, \
    repack_with_argument_clinic, AbstractArguments, TreeArgumentsWrapper
from jedi.inference import analysis
from jedi.inference import compiled
from jedi.inference.value.instance import BoundMethod, InstanceArguments
from jedi.inference.base_value import ValueualizedNode, \
    NO_VALUES, ValueSet, ValueWrapper, LazyValueWrapper
from jedi.inference.value import ClassValue, ModuleValue, \
    FunctionExecutionValue
from jedi.inference.value.klass import ClassMixin
from jedi.inference.value.function import FunctionMixin
from jedi.inference.value import iterable
from jedi.inference.lazy_value import LazyTreeValue, LazyKnownValue, \
    LazyKnownValues
from jedi.inference.names import ValueName, BaseTreeParamName
from jedi.inference.syntax_tree import is_string
from jedi.inference.filters import AttributeOverwrite, publish_method, \
    ParserTreeFilter, DictFilter
from jedi.inference.signature import AbstractSignature, SignatureWrapper


# Copied from Python 3.6's stdlib.
_NAMEDTUPLE_CLASS_TEMPLATE = """\
_property = property
_tuple = tuple
from operator import itemgetter as _itemgetter
from collections import OrderedDict

class {typename}(tuple):
    '{typename}({arg_list})'

    __slots__ = ()

    _fields = {field_names!r}

    def __new__(_cls, {arg_list}):
        'Create new instance of {typename}({arg_list})'
        return _tuple.__new__(_cls, ({arg_list}))

    @classmethod
    def _make(cls, iterable, new=tuple.__new__, len=len):
        'Make a new {typename} object from a sequence or iterable'
        result = new(cls, iterable)
        if len(result) != {num_fields:d}:
            raise TypeError('Expected {num_fields:d} arguments, got %d' % len(result))
        return result

    def _replace(_self, **kwds):
        'Return a new {typename} object replacing specified fields with new values'
        result = _self._make(map(kwds.pop, {field_names!r}, _self))
        if kwds:
            raise ValueError('Got unexpected field names: %r' % list(kwds))
        return result

    def __repr__(self):
        'Return a nicely formatted representation string'
        return self.__class__.__name__ + '({repr_fmt})' % self

    def _asdict(self):
        'Return a new OrderedDict which maps field names to their values.'
        return OrderedDict(zip(self._fields, self))

    def __getnewargs__(self):
        'Return self as a plain tuple.  Used by copy and pickle.'
        return tuple(self)

    # These methods were added by Jedi.
    # __new__ doesn't really work with Jedi. So adding this to nametuples seems
    # like the easiest way.
    def __init__(_cls, {arg_list}):
        'A helper function for namedtuple.'
        self.__iterable = ({arg_list})

    def __iter__(self):
        for i in self.__iterable:
            yield i

    def __getitem__(self, y):
        return self.__iterable[y]

{field_defs}
"""

_NAMEDTUPLE_FIELD_TEMPLATE = '''\
    {name} = _property(_itemgetter({index:d}), doc='Alias for field number {index:d}')
'''


def execute(callback):
    def wrapper(value, arguments):
        def call():
            return callback(value, arguments=arguments)

        try:
            obj_name = value.name.string_name
        except AttributeError:
            pass
        else:
            if value.parent_context == value.infer_state.builtins_module:
                module_name = 'builtins'
            elif value.parent_context is not None and value.parent_context.is_module():
                module_name = value.parent_context.py__name__()
            else:
                return call()

            if isinstance(value, BoundMethod):
                if module_name == 'builtins':
                    if value.py__name__() == '__get__':
                        if value.class_value.py__name__() == 'property':
                            return builtins_property(
                                value,
                                arguments=arguments,
                                callback=call,
                            )
                    elif value.py__name__() in ('deleter', 'getter', 'setter'):
                        if value.class_value.py__name__() == 'property':
                            return ValueSet([value.instance])

                return call()

            # for now we just support builtin functions.
            try:
                func = _implemented[module_name][obj_name]
            except KeyError:
                pass
            else:
                return func(value, arguments=arguments, callback=call)
        return call()

    return wrapper


def _follow_param(infer_state, arguments, index):
    try:
        key, lazy_value = list(arguments.unpack())[index]
    except IndexError:
        return NO_VALUES
    else:
        return lazy_value.infer()


def argument_clinic(string, want_obj=False, want_value=False,
                    want_arguments=False, want_infer_state=False,
                    want_callback=False):
    """
    Works like Argument Clinic (PEP 436), to validate function params.
    """

    def f(func):
        @repack_with_argument_clinic(string, keep_arguments_param=True,
                                     keep_callback_param=True)
        def wrapper(obj, *args, **kwargs):
            arguments = kwargs.pop('arguments')
            callback = kwargs.pop('callback')
            assert not kwargs  # Python 2...
            debug.dbg('builtin start %s' % obj, color='MAGENTA')
            result = NO_VALUES
            if want_value:
                kwargs['value'] = arguments.value
            if want_obj:
                kwargs['obj'] = obj
            if want_infer_state:
                kwargs['infer_state'] = obj.infer_state
            if want_arguments:
                kwargs['arguments'] = arguments
            if want_callback:
                kwargs['callback'] = callback
            result = func(*args, **kwargs)
            debug.dbg('builtin end: %s', result, color='MAGENTA')
            return result

        return wrapper
    return f


@argument_clinic('obj, type, /', want_obj=True, want_arguments=True)
def builtins_property(objects, types, obj, arguments):
    property_args = obj.instance.var_args.unpack()
    key, lazy_value = next(property_args, (None, None))
    if key is not None or lazy_value is None:
        debug.warning('property expected a first param, not %s', arguments)
        return NO_VALUES

    return lazy_value.infer().py__call__(arguments=ValuesArguments([objects]))


@argument_clinic('iterator[, default], /', want_infer_state=True)
def builtins_next(iterators, defaults, infer_state):
    if infer_state.environment.version_info.major == 2:
        name = 'next'
    else:
        name = '__next__'

    # TODO theoretically we have to check here if something is an iterator.
    # That is probably done by checking if it's not a class.
    return defaults | iterators.py__getattribute__(name).execute_with_values()


@argument_clinic('iterator[, default], /')
def builtins_iter(iterators_or_callables, defaults):
    # TODO implement this if it's a callable.
    return iterators_or_callables.py__getattribute__('__iter__').execute_with_values()


@argument_clinic('object, name[, default], /')
def builtins_getattr(objects, names, defaults=None):
    # follow the first param
    for obj in objects:
        for name in names:
            string = get_str_or_none(name)
            if string is None:
                debug.warning('getattr called without str')
                continue
            else:
                return obj.py__getattribute__(force_unicode(string))
    return NO_VALUES


@argument_clinic('object[, bases, dict], /')
def builtins_type(objects, bases, dicts):
    if bases or dicts:
        # It's a type creation... maybe someday...
        return NO_VALUES
    else:
        return objects.py__class__()


class SuperInstance(LazyValueWrapper):
    """To be used like the object ``super`` returns."""
    def __init__(self, infer_state, instance):
        self.infer_state = infer_state
        self._instance = instance  # Corresponds to super().__self__

    def _get_bases(self):
        return self._instance.py__class__().py__bases__()

    def _get_wrapped_value(self):
        objs = self._get_bases()[0].infer().execute_with_values()
        if not objs:
            # This is just a fallback and will only be used, if it's not
            # possible to find a class
            return self._instance
        return next(iter(objs))

    def get_filters(self, search_global=False, until_position=None, origin_scope=None):
        for b in self._get_bases():
            for obj in b.infer().execute_with_values():
                for f in obj.get_filters():
                    yield f


@argument_clinic('[type[, obj]], /', want_value=True)
def builtins_super(types, objects, value):
    if isinstance(value, FunctionExecutionValue):
        if isinstance(value.var_args, InstanceArguments):
            instance = value.var_args.instance
            # TODO if a class is given it doesn't have to be the direct super
            #      class, it can be an anecestor from long ago.
            return ValueSet({SuperInstance(instance.infer_state, instance)})

    return NO_VALUES


class ReversedObject(AttributeOverwrite):
    def __init__(self, reversed_obj, iter_list):
        super(ReversedObject, self).__init__(reversed_obj)
        self._iter_list = iter_list

    @publish_method('__iter__')
    def py__iter__(self, valueualized_node=None):
        return self._iter_list

    @publish_method('next', python_version_match=2)
    @publish_method('__next__', python_version_match=3)
    def py__next__(self):
        return ValueSet.from_sets(
            lazy_value.infer() for lazy_value in self._iter_list
        )


@argument_clinic('sequence, /', want_obj=True, want_arguments=True)
def builtins_reversed(sequences, obj, arguments):
    # While we could do without this variable (just by using sequences), we
    # want static analysis to work well. Therefore we need to generated the
    # values again.
    key, lazy_value = next(arguments.unpack())
    cn = None
    if isinstance(lazy_value, LazyTreeValue):
        # TODO access private
        cn = ValueualizedNode(lazy_value.value, lazy_value.data)
    ordered = list(sequences.iterate(cn))

    # Repack iterator values and then run it the normal way. This is
    # necessary, because `reversed` is a function and autocompletion
    # would fail in certain cases like `reversed(x).__iter__` if we
    # just returned the result directly.
    seq, = obj.infer_state.typing_module.py__getattribute__('Iterator').execute_with_values()
    return ValueSet([ReversedObject(seq, list(reversed(ordered)))])


@argument_clinic('obj, type, /', want_arguments=True, want_infer_state=True)
def builtins_isinstance(objects, types, arguments, infer_state):
    bool_results = set()
    for o in objects:
        cls = o.py__class__()
        try:
            cls.py__bases__
        except AttributeError:
            # This is temporary. Everything should have a class attribute in
            # Python?! Maybe we'll leave it here, because some numpy objects or
            # whatever might not.
            bool_results = set([True, False])
            break

        mro = list(cls.py__mro__())

        for cls_or_tup in types:
            if cls_or_tup.is_class():
                bool_results.add(cls_or_tup in mro)
            elif cls_or_tup.name.string_name == 'tuple' \
                    and cls_or_tup.get_root_value() == infer_state.builtins_module:
                # Check for tuples.
                classes = ValueSet.from_sets(
                    lazy_value.infer()
                    for lazy_value in cls_or_tup.iterate()
                )
                bool_results.add(any(cls in mro for cls in classes))
            else:
                _, lazy_value = list(arguments.unpack())[1]
                if isinstance(lazy_value, LazyTreeValue):
                    node = lazy_value.data
                    message = 'TypeError: isinstance() arg 2 must be a ' \
                              'class, type, or tuple of classes and types, ' \
                              'not %s.' % cls_or_tup
                    analysis.add(lazy_value.value, 'type-error-isinstance', node, message)

    return ValueSet(
        compiled.builtin_from_name(infer_state, force_unicode(str(b)))
        for b in bool_results
    )


class StaticMethodObject(AttributeOverwrite, ValueWrapper):
    def get_object(self):
        return self._wrapped_value

    def py__get__(self, instance, klass):
        return ValueSet([self._wrapped_value])


@argument_clinic('sequence, /')
def builtins_staticmethod(functions):
    return ValueSet(StaticMethodObject(f) for f in functions)


class ClassMethodObject(AttributeOverwrite, ValueWrapper):
    def __init__(self, class_method_obj, function):
        super(ClassMethodObject, self).__init__(class_method_obj)
        self._function = function

    def get_object(self):
        return self._wrapped_value

    def py__get__(self, obj, class_value):
        return ValueSet([
            ClassMethodGet(__get__, class_value, self._function)
            for __get__ in self._wrapped_value.py__getattribute__('__get__')
        ])


class ClassMethodGet(AttributeOverwrite, ValueWrapper):
    def __init__(self, get_method, klass, function):
        super(ClassMethodGet, self).__init__(get_method)
        self._class = klass
        self._function = function

    def get_signatures(self):
        return self._function.get_signatures()

    def get_object(self):
        return self._wrapped_value

    def py__call__(self, arguments):
        return self._function.execute(ClassMethodArguments(self._class, arguments))


class ClassMethodArguments(TreeArgumentsWrapper):
    def __init__(self, klass, arguments):
        super(ClassMethodArguments, self).__init__(arguments)
        self._class = klass

    def unpack(self, func=None):
        yield None, LazyKnownValue(self._class)
        for values in self._wrapped_arguments.unpack(func):
            yield values


@argument_clinic('sequence, /', want_obj=True, want_arguments=True)
def builtins_classmethod(functions, obj, arguments):
    return ValueSet(
        ClassMethodObject(class_method_object, function)
        for class_method_object in obj.py__call__(arguments=arguments)
        for function in functions
    )


def collections_namedtuple(obj, arguments, callback):
    """
    Implementation of the namedtuple function.

    This has to be done by processing the namedtuple class template and
    inferring the result.

    """
    infer_state = obj.infer_state

    # Process arguments
    name = u'jedi_unknown_namedtuple'
    for c in _follow_param(infer_state, arguments, 0):
        x = get_str_or_none(c)
        if x is not None:
            name = force_unicode(x)
            break

    # TODO here we only use one of the types, we should use all.
    param_values = _follow_param(infer_state, arguments, 1)
    if not param_values:
        return NO_VALUES
    _fields = list(param_values)[0]
    string = get_str_or_none(_fields)
    if string is not None:
        fields = force_unicode(string).replace(',', ' ').split()
    elif isinstance(_fields, iterable.Sequence):
        fields = [
            force_unicode(get_str_or_none(v))
            for lazy_value in _fields.py__iter__()
            for v in lazy_value.infer()
        ]
        fields = [f for f in fields if f is not None]
    else:
        return NO_VALUES

    # Build source code
    code = _NAMEDTUPLE_CLASS_TEMPLATE.format(
        typename=name,
        field_names=tuple(fields),
        num_fields=len(fields),
        arg_list=repr(tuple(fields)).replace("u'", "").replace("'", "")[1:-1],
        repr_fmt='',
        field_defs='\n'.join(_NAMEDTUPLE_FIELD_TEMPLATE.format(index=index, name=name)
                             for index, name in enumerate(fields))
    )

    # Parse source code
    module = infer_state.grammar.parse(code)
    generated_class = next(module.iter_classdefs())
    parent_context = ModuleValue(
        infer_state, module,
        file_io=None,
        string_names=None,
        code_lines=parso.split_lines(code, keepends=True),
    )

    return ValueSet([ClassValue(infer_state, parent_context, generated_class)])


class PartialObject(object):
    def __init__(self, actual_value, arguments):
        self._actual_value = actual_value
        self._arguments = arguments

    def __getattr__(self, name):
        return getattr(self._actual_value, name)

    def _get_function(self, unpacked_arguments):
        key, lazy_value = next(unpacked_arguments, (None, None))
        if key is not None or lazy_value is None:
            debug.warning("Partial should have a proper function %s", self._arguments)
            return None
        return lazy_value.infer()

    def get_signatures(self):
        unpacked_arguments = self._arguments.unpack()
        func = self._get_function(unpacked_arguments)
        if func is None:
            return []

        arg_count = 0
        keys = set()
        for key, _ in unpacked_arguments:
            if key is None:
                arg_count += 1
            else:
                keys.add(key)
        return [PartialSignature(s, arg_count, keys) for s in func.get_signatures()]

    def py__call__(self, arguments):
        func = self._get_function(self._arguments.unpack())
        if func is None:
            return NO_VALUES

        return func.execute(
            MergedPartialArguments(self._arguments, arguments)
        )


class PartialSignature(SignatureWrapper):
    def __init__(self, wrapped_signature, skipped_arg_count, skipped_arg_set):
        super(PartialSignature, self).__init__(wrapped_signature)
        self._skipped_arg_count = skipped_arg_count
        self._skipped_arg_set = skipped_arg_set

    def get_param_names(self, resolve_stars=False):
        names = self._wrapped_signature.get_param_names()[self._skipped_arg_count:]
        return [n for n in names if n.string_name not in self._skipped_arg_set]


class MergedPartialArguments(AbstractArguments):
    def __init__(self, partial_arguments, call_arguments):
        self._partial_arguments = partial_arguments
        self._call_arguments = call_arguments

    def unpack(self, funcdef=None):
        unpacked = self._partial_arguments.unpack(funcdef)
        # Ignore this one, it's the function. It was checked before that it's
        # there.
        next(unpacked)
        for key_lazy_value in unpacked:
            yield key_lazy_value
        for key_lazy_value in self._call_arguments.unpack(funcdef):
            yield key_lazy_value


def functools_partial(obj, arguments, callback):
    return ValueSet(
        PartialObject(instance, arguments)
        for instance in obj.py__call__(arguments)
    )


@argument_clinic('first, /')
def _return_first_param(firsts):
    return firsts


@argument_clinic('seq')
def _random_choice(sequences):
    return ValueSet.from_sets(
        lazy_value.infer()
        for sequence in sequences
        for lazy_value in sequence.py__iter__()
    )


def _dataclass(obj, arguments, callback):
    for c in _follow_param(obj.infer_state, arguments, 0):
        if c.is_class():
            return ValueSet([DataclassWrapper(c)])
        else:
            return ValueSet([obj])
    return NO_VALUES


class DataclassWrapper(ValueWrapper, ClassMixin):
    def get_signatures(self):
        param_names = []
        for cls in reversed(list(self.py__mro__())):
            if isinstance(cls, DataclassWrapper):
                filter_ = cls.get_global_filter()
                # .values ordering is not guaranteed, at least not in
                # Python < 3.6, when dicts where not ordered, which is an
                # implementation detail anyway.
                for name in sorted(filter_.values(), key=lambda name: name.start_pos):
                    d = name.tree_name.get_definition()
                    annassign = d.children[1]
                    if d.type == 'expr_stmt' and annassign.type == 'annassign':
                        if len(annassign.children) < 4:
                            default = None
                        else:
                            default = annassign.children[3]
                        param_names.append(DataclassParamName(
                            parent_context=cls.parent_context,
                            tree_name=name.tree_name,
                            annotation_node=annassign.children[1],
                            default_node=default,
                        ))
        return [DataclassSignature(cls, param_names)]


class DataclassSignature(AbstractSignature):
    def __init__(self, value, param_names):
        super(DataclassSignature, self).__init__(value)
        self._param_names = param_names

    def get_param_names(self, resolve_stars=False):
        return self._param_names


class DataclassParamName(BaseTreeParamName):
    def __init__(self, parent_context, tree_name, annotation_node, default_node):
        super(DataclassParamName, self).__init__(parent_context, tree_name)
        self.annotation_node = annotation_node
        self.default_node = default_node

    def get_kind(self):
        return Parameter.POSITIONAL_OR_KEYWORD

    def infer(self):
        if self.annotation_node is None:
            return NO_VALUES
        else:
            return self.parent_context.infer_node(self.annotation_node)


class ItemGetterCallable(ValueWrapper):
    def __init__(self, instance, args_value_set):
        super(ItemGetterCallable, self).__init__(instance)
        self._args_value_set = args_value_set

    @repack_with_argument_clinic('item, /')
    def py__call__(self, item_value_set):
        value_set = NO_VALUES
        for args_value in self._args_value_set:
            lazy_values = list(args_value.py__iter__())
            if len(lazy_values) == 1:
                # TODO we need to add the valueualized value.
                value_set |= item_value_set.get_item(lazy_values[0].infer(), None)
            else:
                value_set |= ValueSet([iterable.FakeSequence(
                    self._wrapped_value.infer_state,
                    'list',
                    [
                        LazyKnownValues(item_value_set.get_item(lazy_value.infer(), None))
                        for lazy_value in lazy_values
                    ],
                )])
        return value_set


@argument_clinic('func, /')
def _functools_wraps(funcs):
    return ValueSet(WrapsCallable(func) for func in funcs)


class WrapsCallable(ValueWrapper):
    # XXX this is not the correct wrapped value, it should be a weird
    #     partials object, but it doesn't matter, because it's always used as a
    #     decorator anyway.
    @repack_with_argument_clinic('func, /')
    def py__call__(self, funcs):
        return ValueSet({Wrapped(func, self._wrapped_value) for func in funcs})


class Wrapped(ValueWrapper, FunctionMixin):
    def __init__(self, func, original_function):
        super(Wrapped, self).__init__(func)
        self._original_function = original_function

    @property
    def name(self):
        return self._original_function.name

    def get_signature_functions(self):
        return [self]


@argument_clinic('*args, /', want_obj=True, want_arguments=True)
def _operator_itemgetter(args_value_set, obj, arguments):
    return ValueSet([
        ItemGetterCallable(instance, args_value_set)
        for instance in obj.py__call__(arguments)
    ])


def _create_string_input_function(func):
    @argument_clinic('string, /', want_obj=True, want_arguments=True)
    def wrapper(strings, obj, arguments):
        def iterate():
            for value in strings:
                s = get_str_or_none(value)
                if s is not None:
                    s = func(s)
                    yield compiled.create_simple_object(value.infer_state, s)
        values = ValueSet(iterate())
        if values:
            return values
        return obj.py__call__(arguments)
    return wrapper


@argument_clinic('*args, /', want_callback=True)
def _os_path_join(args_set, callback):
    if len(args_set) == 1:
        string = u''
        sequence, = args_set
        is_first = True
        for lazy_value in sequence.py__iter__():
            string_values = lazy_value.infer()
            if len(string_values) != 1:
                break
            s = get_str_or_none(next(iter(string_values)))
            if s is None:
                break
            if not is_first:
                string += os.path.sep
            string += force_unicode(s)
            is_first = False
        else:
            return ValueSet([compiled.create_simple_object(sequence.infer_state, string)])
    return callback()


_implemented = {
    'builtins': {
        'getattr': builtins_getattr,
        'type': builtins_type,
        'super': builtins_super,
        'reversed': builtins_reversed,
        'isinstance': builtins_isinstance,
        'next': builtins_next,
        'iter': builtins_iter,
        'staticmethod': builtins_staticmethod,
        'classmethod': builtins_classmethod,
    },
    'copy': {
        'copy': _return_first_param,
        'deepcopy': _return_first_param,
    },
    'json': {
        'load': lambda obj, arguments, callback: NO_VALUES,
        'loads': lambda obj, arguments, callback: NO_VALUES,
    },
    'collections': {
        'namedtuple': collections_namedtuple,
    },
    'functools': {
        'partial': functools_partial,
        'wraps': _functools_wraps,
    },
    '_weakref': {
        'proxy': _return_first_param,
    },
    'random': {
        'choice': _random_choice,
    },
    'operator': {
        'itemgetter': _operator_itemgetter,
    },
    'abc': {
        # Not sure if this is necessary, but it's used a lot in typeshed and
        # it's for now easier to just pass the function.
        'abstractmethod': _return_first_param,
    },
    'typing': {
        # The _alias function just leads to some annoying type inference.
        # Therefore, just make it return nothing, which leads to the stubs
        # being used instead. This only matters for 3.7+.
        '_alias': lambda obj, arguments, callback: NO_VALUES,
    },
    'dataclasses': {
        # For now this works at least better than Jedi trying to understand it.
        'dataclass': _dataclass
    },
    'os.path': {
        'dirname': _create_string_input_function(os.path.dirname),
        'abspath': _create_string_input_function(os.path.abspath),
        'relpath': _create_string_input_function(os.path.relpath),
        'join': _os_path_join,
    }
}


def get_metaclass_filters(func):
    def wrapper(cls, metaclasses):
        for metaclass in metaclasses:
            if metaclass.py__name__() == 'EnumMeta' \
                    and metaclass.get_root_value().py__name__() == 'enum':
                filter_ = ParserTreeFilter(value=cls)
                return [DictFilter({
                    name.string_name: EnumInstance(cls, name).name for name in filter_.values()
                })]
        return func(cls, metaclasses)
    return wrapper


class EnumInstance(LazyValueWrapper):
    def __init__(self, cls, name):
        self.infer_state = cls.infer_state
        self._cls = cls  # Corresponds to super().__self__
        self._name = name
        self.tree_node = self._name.tree_name

    @safe_property
    def name(self):
        return ValueName(self, self._name.tree_name)

    def _get_wrapped_value(self):
        obj, = self._cls.execute_with_values()
        return obj

    def get_filters(self, search_global=False, position=None, origin_scope=None):
        yield DictFilter(dict(
            name=compiled.create_simple_object(self.infer_state, self._name.string_name).name,
            value=self._name,
        ))
        for f in self._get_wrapped_value().get_filters():
            yield f


def tree_name_to_values(func):
    def wrapper(infer_state, value, tree_name):
        if tree_name.value == 'sep' and value.is_module() and value.py__name__() == 'os.path':
            return ValueSet({
                compiled.create_simple_object(infer_state, os.path.sep),
            })
        return func(infer_state, value, tree_name)
    return wrapper

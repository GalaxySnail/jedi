import textwrap
from inspect import cleandoc

from jedi._compatibility import literal_eval, is_py3
from jedi.parser.python import tree

_EXECUTE_NODES = set([
    'funcdef', 'classdef', 'import_from', 'import_name', 'test', 'or_test',
    'and_test', 'not_test', 'comparison', 'expr', 'xor_expr', 'and_expr',
    'shift_expr', 'arith_expr', 'atom_expr', 'term', 'factor', 'power', 'atom'
])

_FLOW_KEYWORDS = (
    'try', 'except', 'finally', 'else', 'if', 'elif', 'with', 'for', 'while'
)


def get_executable_nodes(node, last_added=False):
    """
    For static analysis.
    """
    result = []
    typ = node.type
    if typ == 'name':
        next_leaf = node.get_next_leaf()
        if last_added is False and node.parent.type != 'param' and next_leaf != '=':
            result.append(node)
    elif typ == 'expr_stmt':
        # I think evaluating the statement (and possibly returned arrays),
        # should be enough for static analysis.
        result.append(node)
        for child in node.children:
            result += get_executable_nodes(child, last_added=True)
    elif typ == 'decorator':
        # decorator
        if node.children[-2] == ')':
            node = node.children[-3]
            if node != '(':
                result += get_executable_nodes(node)
    else:
        try:
            children = node.children
        except AttributeError:
            pass
        else:
            if node.type in _EXECUTE_NODES and not last_added:
                result.append(node)

            for child in children:
                result += get_executable_nodes(child, last_added)

    return result


def get_comp_fors(comp_for):
    yield comp_for
    last = comp_for.children[-1]
    while True:
        if last.type == 'comp_for':
            yield last
        elif not last.type == 'comp_if':
            break
        last = last.children[-1]


def for_stmt_defines_one_name(for_stmt):
    """
    Returns True if only one name is returned: ``for x in y``.
    Returns False if the for loop is more complicated: ``for x, z in y``.

    :returns: bool
    """
    return for_stmt.children[1].type == 'name'


def get_flow_branch_keyword(flow_node, node):
    start_pos = node.start_pos
    if not (flow_node.start_pos < start_pos <= flow_node.end_pos):
        raise ValueError('The node is not part of the flow.')

    keyword = None
    for i, child in enumerate(flow_node.children):
        if start_pos < child.start_pos:
            return keyword
        first_leaf = child.get_first_leaf()
        if first_leaf in _FLOW_KEYWORDS:
            keyword = first_leaf
    return 0

def get_statement_of_position(node, pos):
    for c in node.children:
        if c.start_pos <= pos <= c.end_pos:
            if c.type not in ('decorated', 'simple_stmt', 'suite') \
                    and not isinstance(c, (tree.Flow, tree.ClassOrFunc)):
                return c
            else:
                try:
                    return get_statement_of_position(c, pos)
                except AttributeError:
                    pass  # Must be a non-scope
    return None


def clean_scope_docstring(scope_node):
    """ Returns a cleaned version of the docstring token. """
    node = scope_node.get_doc_node()
    if node is not None:
        # TODO We have to check next leaves until there are no new
        # leaves anymore that might be part of the docstring. A
        # docstring can also look like this: ``'foo' 'bar'
        # Returns a literal cleaned version of the ``Token``.
        cleaned = cleandoc(safe_literal_eval(node.value))
        # Since we want the docstr output to be always unicode, just
        # force it.
        if is_py3 or isinstance(cleaned, unicode):
            return cleaned
        else:
            return unicode(cleaned, 'UTF-8', 'replace')
    return ''


def safe_literal_eval(value):
    first_two = value[:2].lower()
    if first_two[0] == 'f' or first_two in ('fr', 'rf'):
        # literal_eval is not able to resovle f literals. We have to do that
        # manually, but that's right now not implemented.
        return ''

    try:
        return literal_eval(value)
    except SyntaxError:
        # It's possible to create syntax errors with literals like rb'' in
        # Python 2. This should not be possible and in that case just return an
        # empty string.
        # Before Python 3.3 there was a more strict definition in which order
        # you could define literals.
        return ''


def get_call_signature(funcdef, width=72, call_string=None):
    """
    Generate call signature of this function.

    :param width: Fold lines if a line is longer than this value.
    :type width: int
    :arg func_name: Override function name when given.
    :type func_name: str

    :rtype: str
    """
    # Lambdas have no name.
    if call_string is None:
        if funcdef.type == 'lambdef':
            call_string = '<lambda>'
        else:
            call_string = funcdef.name.value
    code = call_string + funcdef._get_paramlist_code()
    return '\n'.join(textwrap.wrap(code, width))


def get_doc_with_call_signature(scope_node):
    """
    Return a document string including call signature.
    """
    call_signature = None
    if scope_node.type == 'classdef':
        for sub in scope_node.subscopes:
            if sub.name.value == '__init__':
                call_signature = \
                    get_call_signature(sub, call_string=scope_node.name.value)
    elif scope_node.type in ('funcdef', 'lambdef'):
        call_signature = get_call_signature(scope_node)

    doc = clean_scope_docstring(scope_node)
    if call_signature is None:
        return doc
    return '%s\n\n%s' % (call_signature, doc)

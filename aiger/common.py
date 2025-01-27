import operator as op
from collections import defaultdict, deque
from functools import reduce
from itertools import starmap
from uuid import uuid1
import funcy as fn

from aiger import aig


def _fresh():
    return str(uuid1())


def _map_tree(inputs, f):
    queue = fn.lmap(aig.Input, inputs)
    while len(queue) > 1:
        queue = fn.lmap(f, fn.chunks(2, queue))
    return queue[0]


def _and(left_right):
    if len(left_right) == 1:
        return left_right[0]

    return aig.AndGate(*left_right)


def and_gate(inputs, output=None):
    output = f'#and_output#{hash(tuple(inputs))}' if output is None else output

    return aig.AIG(
        inputs=frozenset(inputs),
        node_map=frozenset(((output, _map_tree(inputs, f=_and)), )),
    )


def or_gate(inputs, output=None):
    output = f'#or_output#{hash(tuple(inputs))}' if output is None else output
    circ = and_gate(inputs, output)

    return bit_flipper(inputs) >> circ >> bit_flipper([output])


def _xor(left_right):
    if len(left_right) == 1:
        return left_right[0]

    return aig.AndGate(
        aig.Inverter(aig.AndGate(*left_right)),  # Both True
        aig.Inverter(aig.AndGate(*map(aig.Inverter, left_right)))  # Both False
    )


def parity_gate(inputs, output=None):
    # TODO:
    output = f'#parity#{hash(tuple(inputs))}' if output is None else output

    return aig.AIG(
        inputs=frozenset(inputs),
        node_map=frozenset(((output, _map_tree(inputs, f=_xor)), )),
    )


def identity(inputs, outputs=None):
    if outputs is None:
        outputs = inputs

    return aig.AIG(
        inputs=frozenset(inputs),
        node_map=frozenset(zip(outputs, map(aig.Input, inputs))),
    )


def empty():
    return aig.AIG()


def _inverted_input(name):
    return aig.Inverter(aig.Input(name))


def bit_flipper(inputs, outputs=None):
    if outputs is None:
        outputs = inputs
    else:
        assert len(outputs) == len(inputs)

    return aig.AIG(
        inputs=frozenset(inputs),
        node_map=frozenset(zip(outputs, map(_inverted_input, inputs))),
    )


def _const(val):
    return aig.Inverter(aig.ConstFalse()) if val else aig.ConstFalse()


def source(outputs):
    return aig.AIG(
        node_map=frozenset((k, _const(v)) for k, v in outputs.items()),
    )


def sink(inputs):
    return aig.AIG(
        inputs=frozenset(inputs),
    )


def tee(outputs=None):
    if not outputs:
        return empty()

    def tee_output(name, renames):
        return frozenset((r, aig.Input(name)) for r in renames)

    return aig.AIG(
        inputs=frozenset(outputs),
        node_map=frozenset.union(*starmap(tee_output, outputs.items())),
    )


def _ite(test: str, in1: str, in0: str, output: str = None):
    r"test -> in1 /\ ~test -> in0"
    assert len({test, in0, in1}) == 3

    true_out = bit_flipper([test]) >> or_gate([test, in1], 'true_out')
    false_out = or_gate([test, in0], 'false_out')
    return (true_out | false_out) >> and_gate(['true_out', 'false_out'],
                                              output)


def ite(test, inputs1, inputs0, outputs):
    assert len(inputs1) > 0
    assert len(inputs1) == len(inputs0) == len(outputs)
    assert len({test} | set(inputs1) | set(inputs0)) == 2 * len(inputs0) + 1

    ites = [_ite(test, *args) for args in zip(inputs1, inputs0, outputs)]
    return reduce(op.or_, ites)


def delay(inputs, initials, latches=None, outputs=None):
    if outputs is None:
        outputs = inputs

    if latches is None:
        latches = inputs

    assert len(inputs) == len(initials) == len(outputs) == len(latches)

    _inputs = map(aig.Input, inputs)
    _latches = map(aig.LatchIn, latches)
    return aig.AIG(
        inputs=frozenset(inputs),
        latch_map=zip(latches, _inputs),
        latch2init=zip(latches, initials),
        node_map=zip(outputs, _latches),
    )


def _dependency_graph(nodes):
    queue, deps, visited = list(nodes), defaultdict(set), set()
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        else:
            visited.add(node)

        children = node.children
        queue.extend(children)
        deps[node].update(children)

    return deps


def dfs(circ):
    """Generates nodes via depth first traversal in pre-order."""
    print("Running DFS")
    cones = circ.cones_set
    if len(circ.latch_cones_set) > 0:
        cones = cones | circ.latch_cones_set
    stack = list(cones)

    emitted = set()
    while stack:
        key = stack.pop()
        node = circ.get_node(key)
        key = node.id
        if key in emitted:
            continue
        if hasattr(node, 'children'):
            ch = node.children
        else:
            ch = []

        children = set(map(lambda x: x.id, filter(lambda x: x.id not in emitted, ch)))

        if children <= emitted:
            yield node
            emitted.add(node.id)
            continue

        stack.append(key)  # Add to emit after children.
        stack.extend(children)
    print("DFS completed for total nodes ", len(emitted))


def topsort(data):
    """Topological-sort of a graph.
    Dependencies are represented in a dictionary with keys being the node and
    values being an iterable of the node's dependencies. The return value is an
    iterable over the nodes in topological order.
    """
    data_t = defaultdict(list)  # transposed graph
    for node, deps in data.items():
        for dep in deps:
            data_t[dep].append(node)
    nodes = set(data) | set(data_t)
    in_deg = ({node: len(data.get(node, ())) for node in nodes})
    queue = deque(node for node, deg in in_deg.items() if deg == 0)
    while queue:
        node = queue.popleft()
        yield node
        for child in data_t.get(node, ()):
            in_deg[child] -= 1
            if in_deg[child] == 0:
                queue.append(child)


def eval_order(circ):
    """Return topologically sorted nodes in AIG."""
    return list(topsort(_dependency_graph(circ.cones | circ.latch_cones)))

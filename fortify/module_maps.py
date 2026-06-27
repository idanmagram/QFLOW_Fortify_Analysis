# This file has been taken from the earlier SOLOMON project, and modified for this project.
# This file contains the functions to parse a Verilogfile, and populate various mappings
# with the details of the module under consideration.

import time
import copy
import pyverilog.vparser.ast as vast
from pyverilog.vparser.parser import parse
import utils
import generate_z3

SHIFT_UNROLL_LIMIT = 128  # max steps to unroll simple shift-register patterns
ARITH_CARRY_LIMIT = 8  # bound adder/subtractor expression growth for QIF extraction

# map for efficiently calculating truth table entries
truthTableMap = {}
# list of signal names
signalNames = set()
# map from signal name to width
sigWidths = {}
# map from module name to AST
moduleAstMap = {}
# map from module name to input ports list
moduleInputPortListMap = {}
# map from module name to output ports list
moduleOutputPortListMap = {}
# map from module name to input ports widths
moduleInputPortWidthListMap = {}
# map from module name to output ports widths
moduleOutputPortWidthListMap = {}
# map from module name to wires list
moduleWireListMap = {}
# map from module name to wires widths
moduleWireWidthListMap = {}
# map from instance name to input ports list - lhs and rhs
instPortInputsMap = {}
# map from instance name to output ports list - lhs and rhs
instPortOutputsMap = {}
# map from module name to wire expr
moduleWireExprMap = {}
# map from module name to wire width
moduleWireWidthMap = {}
# bases assigned via sequential (nonblocking) statements
seqBases = set()


def set_arith_carry_limit(limit):
    global ARITH_CARRY_LIMIT
    ARITH_CARRY_LIMIT = max(0, int(limit))
LARGE_CONST_LUT_MIN_CASES = 11


def simplify_large_const_lut_cond(expr, min_cases=LARGE_CONST_LUT_MIN_CASES, target_bit_idx=None, clk_name=None):
    """
    Detect large constant-LUT Cond chains and collapse them into an exact
    per-bit LUT node. This is used for constructs such as:

        always @(posedge clk)
            case (in)
                ...
                out <= <constant>;

    The probability engine can then evaluate the output bit exactly from the
    input-bit probabilities.
    """
    def _is_bit_const(v):
        return isinstance(v, int) and v in (0, 1)

    cases = []
    sel_bus = None
    cur = expr

    while isinstance(cur, list) and len(cur) == 4 and cur[0] == "Cond":
        cond, tval, fval = cur[1], cur[2], cur[3]
        if not (isinstance(cond, list) and len(cond) >= 3 and cond[0] in ("EqBus", "Eq")):
            return expr
        if cond[0] == "EqBus":
            bus = cond[1]
            key = cond[2]
            if not isinstance(bus, str) or not isinstance(key, int):
                return expr
        else:
            a = cond[1] if len(cond) > 1 else None
            b = cond[2] if len(cond) > 2 else None
            if isinstance(a, str) and isinstance(b, int):
                bus, key = a, b
            elif isinstance(b, str) and isinstance(a, int):
                bus, key = b, a
            else:
                return expr
        if sel_bus is None:
            sel_bus = bus
        elif sel_bus != bus:
            return expr
        if not _is_bit_const(tval):
            return expr
        cases.append((key, int(tval)))
        cur = fval

    if not _is_bit_const(cur):
        return expr
    default_bit = int(cur)

    if len(cases) < min_cases or target_bit_idx is None or not isinstance(sel_bus, str):
        return expr

    exception_keys = tuple(key for key, bit in cases if bit != default_bit)
    if isinstance(clk_name, str):
        return ['LutConstBit', sel_bus, default_bit, exception_keys, clk_name]
    return ['LutConstBit', sel_bus, default_bit, exception_keys]



# parses the input Verilog file (along with standard module definitions)
def populateModuleAstMap(file_path):
    global moduleAstMap

    print("Parsing...")
    a = time.time()
    ast, directives = parse([file_path, 'std_cell_lib/std_gates.v', 'std_cell_lib/std_modules.v'])

    b = time.time()

    for item in ast.description.definitions:
        moduleAstMap[item.name] = item

    print("Parsing completed in {:.4f}s".format(b - a))


# creates mappings between modules and their input ports & widths, output ports & widths, wires
# creates mappings between modules and their input ports & widths, output ports & widths, wires
def populateModuleInputOutputPortListMap(moduleAst):
    module_name = moduleAst.name

    moduleInputPortListMap[module_name] = []
    moduleInputPortWidthListMap[module_name] = []
    moduleOutputPortListMap[module_name] = []
    moduleOutputPortWidthListMap[module_name] = []
    moduleWireListMap[module_name] = []
    moduleWireWidthListMap[module_name] = []

    seen_inputs = set()
    seen_outputs = set()
    seen_wires = set()

    def _decl_width(node):
        width = getattr(node, "width", None)
        if width is None:
            return 1
        return (
            utils.verilogIntConstToInt(width.msb)
            - utils.verilogIntConstToInt(width.lsb)
            + 1
        )

    def _add_port(name, width, is_output):
        if is_output:
            if name not in seen_outputs:
                moduleOutputPortListMap[module_name].append(name)
                moduleOutputPortWidthListMap[module_name].append(width)
                seen_outputs.add(name)
        else:
            if name not in seen_inputs:
                moduleInputPortListMap[module_name].append(name)
                moduleInputPortWidthListMap[module_name].append(width)
                seen_inputs.add(name)

    portlist = getattr(moduleAst, "portlist", None)
    for port in getattr(portlist, "ports", []) or []:
        if isinstance(port, vast.Ioport):
            first = getattr(port, "first", None)
            second = getattr(port, "second", None)
            decl = first if isinstance(first, (vast.Input, vast.Output)) else second
            if isinstance(decl, vast.Input):
                _add_port(decl.name, _decl_width(decl), is_output=False)
            elif isinstance(decl, vast.Output):
                _add_port(decl.name, _decl_width(decl), is_output=True)

    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Decl):
            for varAst in itemAst.list:
                width = _decl_width(varAst)

                if isinstance(varAst, vast.Input):
                    _add_port(varAst.name, width, is_output=False)
                elif isinstance(varAst, vast.Output):
                    _add_port(varAst.name, width, is_output=True)
                elif isinstance(varAst, vast.Wire) or isinstance(varAst, vast.Reg):
                    if varAst.name not in seen_wires:
                        moduleWireListMap[module_name].append(varAst.name)
                        moduleWireWidthListMap[module_name].append(width)
                        seen_wires.add(varAst.name)
                    if isinstance(varAst, vast.Reg):
                        if varAst.name in seen_outputs:
                            out_idx = moduleOutputPortListMap[module_name].index(varAst.name)
                            moduleOutputPortWidthListMap[module_name][out_idx] = width

    # PyVerilog may represent declarations such as `output reg [7:0] out;` as a
    # separate `Reg` item without preserving the output width on the `Output`
    # entry. Mirror widths from matching regs onto output ports.
    wire_widths = dict(
        zip(moduleWireListMap[module_name], moduleWireWidthListMap[module_name])
    )
    for idx, out_name in enumerate(moduleOutputPortListMap[module_name]):
        if (
            moduleOutputPortWidthListMap[module_name][idx] == 1
            and out_name in wire_widths
            and wire_widths[out_name] > 1
        ):
            moduleOutputPortWidthListMap[module_name][idx] = wire_widths[out_name]


# recursively gets the list of all instances within an ast
def getInstListFromAst(ast):
    inst_list = []
    if isinstance(ast, vast.Instance):
        inst_list.append(ast)
    for child in ast.children():
        inst_list.extend(getInstListFromAst(child))

    return inst_list


# getting the list of rhs signal names from the ast type, instance name and its width
def getRnames(x, instance_name, wid):
    # Constant literal on a port: e.g. .rcon(8'h01)
    # Expand to a list of wid bits (LSB first)
    if isinstance(x, vast.IntConst):
        const_val = utils.verilogIntConstToInt(x)
        bitstring = format(const_val, '0{}b'.format(wid))[::-1]  # LSB-first
        rnames = [int(b) for b in bitstring]

    elif isinstance(x, vast.Partselect):
        rname = '{}.{}'.format(instance_name, x.var.name)
        lsb = utils.verilogIntConstToInt(x.lsb)
        msb = utils.verilogIntConstToInt(x.msb)
        rnames = ['{}[{}:{}]'.format(rname, j, j) for j in range(lsb, msb + 1)]

    elif isinstance(x, vast.Pointer):
        rname = '{}.{}'.format(instance_name, x.var.name)
        try:
            ptr = utils.verilogIntConstToInt(x.ptr)
            signalName = '{}[{}:{}]'.format(rname, ptr, ptr)
            rnames = [signalName]
        except Exception:
            # dynamic index: approximate as an equal mix over all bits
            # dynamic index: approximate as an equal mix over all bits
            width = sigWidths.get(rname, None)
            if not isinstance(width, int) or width < 1:
                width = sigWidths.get(x.var.name, None)
            if not isinstance(width, int) or width < 1:
                width = sigWidths.get('{}.{}'.format(instance_name, x.var.name), None)
            if not isinstance(width, int) or width < 1:
                width = wid if isinstance(wid, int) and wid > 0 else 1
            rnames = ['Mix'] + ['{}[{}:{}]'.format(rname, j, j) for j in range(width)]

    elif isinstance(x, vast.Concat):
        # Keep existing behavior: list of sub-expressions (strings/bit refs)
        # Higher-level code that sees list will handle concatenation.
        rnames = [getSigName(y, instance_name) for y in x.list[::-1]]

    else:
        # Identifier or other simple expression: treat as wid-bit vector
        # x is usually a vast.Identifier or a name string
        rname = '{}.{}'.format(instance_name, x.name if hasattr(x, 'name') else x)
        rnames = ['{}[{}:{}]'.format(rname, j, j) for j in range(wid)]

    return rnames


# populate the maps between input/output ports and corresponding arguments
def populateInstPortInputOutputMap(inst, instance_name):
    inst_module_name = inst.module
    inst_name = '{}.{}'.format(instance_name, inst.name)

    ins = ['{}.{}'.format(inst_name, port) for port in moduleInputPortListMap[inst_module_name]]
    insWidths = moduleInputPortWidthListMap[inst_module_name][:]
    outs = ['{}.{}'.format(inst_name, w1) for w1 in moduleOutputPortListMap[inst_module_name]]
    outsWidths = moduleOutputPortWidthListMap[inst_module_name][:]

    portnames = [portAst.portname for portAst in inst.portlist]

    if not any(portnames):
        inst.portlist[0].portname = 'o'
        for i in range(1, len(portnames)):
            inst.portlist[i].portname = 'i{}'.format(i)

    all_lnames_inp = []
    all_rnames_inp = []

    all_lnames_out = []
    all_rnames_out = []

    for x in inst.portlist:
        lname = '{}.{}'.format(inst_name, x.portname)
        if lname in ins:
            wid = insWidths[ins.index(lname)]
            sigWidths[lname] = wid

            lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
            rnames = getRnames(x.argname, instance_name, wid)

            all_lnames_inp.extend(lnames)
            all_rnames_inp.extend(rnames)

        if lname in outs:
            wid = outsWidths[outs.index(lname)]
            sigWidths[lname] = wid

            lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
            rnames = getRnames(x.argname, instance_name, wid)

            all_lnames_out.extend(lnames)
            all_rnames_out.extend(rnames)

    instPortInputsMap[inst_name] = {"lnames": all_lnames_inp[:], "rnames": all_rnames_inp[:]}
    instPortOutputsMap[inst_name] = {"lnames": all_lnames_out[:], "rnames": all_rnames_out[:]}


# extract the sub-circuit of the design influenced by the reference signal bits
def extractSubCircuit(module_name, instance_name, ref_sig_bit_names):
    global truthTableMap
    global signalNames
    global moduleInputPortListMap
    global moduleOutputPortListMap
    global moduleInputPortWidthListMap
    global moduleOutputPortWidthListMap
    global moduleWireExprMap
    global moduleWireWidthMap
    global instPortInputsMap
    global instPortOutputsMap

    moduleAst = moduleAstMap[module_name]
    inst_list = getInstListFromAst(moduleAst)

    # populate the input/output port maps for all the instances in the module
    for inst in inst_list:
        populateInstPortInputOutputMap(inst, instance_name)

    # Detect self-driven bases (loops) to keep them even if not reachable from refs.
    self_driven = set()
    for k, v in truthTableMap.items():
        try:
            base = k.split("[", 1)[0]
        except Exception:
            continue

        def _refs_base(expr, b):
            if isinstance(expr, str):
                return expr.split("[", 1)[0] == b if "[" in expr else expr == b
            if isinstance(expr, list):
                return any(_refs_base(e, b) for e in expr[1:])
            return False

        if _refs_base(v, base):
            self_driven.add(k)

    # NEW: Also keep any signal that is a sequential base (register),
    # ensuring we don't prune loops hidden behind wires (like lfsr_stream <= d0).
    # print(f"DEBUG: seqBases sample: {list(seqBases)[:5]}")
    for k in truthTableMap.keys():
        try:
            base = k.split("[", 1)[0]
        except:
            continue

        # Check against seqBases (which are module-local names, e.g. 'lfsr_stream')
        # identifying if the leaf identifier of the hierarchical name is in seqBases
        leaf = base.split('.')[-1]

        if leaf in seqBases:
            self_driven.add(k)

        # Also check exact match just in case
        if base in seqBases:
            self_driven.add(k)

    # keep self-driven signals in the returned set regardless of reachability
    signalNames |= self_driven

    # seed with primary inputs to avoid pruning self-driven fanout
    seed = set(ref_sig_bit_names[:])
    for inp, wid in zip(moduleInputPortListMap[module_name], moduleInputPortWidthListMap[module_name]):
        seed |= {f'{instance_name}.{inp}[{i}:{i}]' for i in range(wid)}

    # the forward set contains the set of signals we want to trace forward
    # initialise this set with the reference signal bits and self-driven loop bits
    forward = seed | self_driven

    # to keep track of the signals which we have already encountered in the tracing
    # to handle cyclic dependencies
    encounteredSigs = forward.copy()

    # set of modules/instances which are found to be part of the sub-circuit of interest
    reqModInst = set()

    # perform the forward tracing as long as there are signals left in the forward set
    while len(forward) > 0:
        # temporary forward set to hold the next set of signals to be traced
        forwardTemp = set()

        # trace each signal in the forward set to see which modules/instances it feeds into
        # get the internal signals of those modules/instances
        # add the outputs of all those modules/instances to the temporary forward set (for next trace)
        for sig in forward:
            for inst in inst_list:
                inst_module_name = inst.module
                inst_name = '{}.{}'.format(instance_name, inst.name)

                if sig in instPortInputsMap[inst_name]["rnames"]:
                    if not (inst_module_name, inst_name) in reqModInst:
                        reqModInst.add((inst_module_name, inst_name))

                        for x in instPortInputsMap[inst_name]["rnames"]:
                            signalNames.add(x)

                        getInternalSignalNames(inst_module_name, inst_name)

                        for x in instPortOutputsMap[inst_name]["rnames"]:
                            forwardTemp.add(x)
                            signalNames.add(x)

        # replace the forward set with the signals in the temporary forward set, while handling the already encountered signals
        forward = set()
        for sig in forwardTemp:
            if not sig in encounteredSigs:
                encounteredSigs.add(sig)
                forward.add(sig)


# constructs the complete signal name(s) from the ast type and instance name
def getSigName(ast, instance_name):
    if isinstance(ast, vast.Identifier):
        sigName = '{}.{}'.format(instance_name, ast.name)
        width = sigWidths.get(sigName)
        if width is None:
            # treat identifiers with unknown width as scalar
            width = 1
        sigName = '{}[{}:{}]'.format(sigName, width - 1, 0)
    elif isinstance(ast, vast.Partselect):
        sigName = '{}.{}[{}:{}]'.format(instance_name, ast.var.name, ast.msb, ast.lsb)
    elif isinstance(ast, vast.Pointer):
        sigName = '{}.{}'.format(instance_name, ast.var.name)
        try:
            ptr = utils.verilogIntConstToInt(ast.ptr)
            sigName = '{}[{}:{}]'.format(sigName, ptr, ptr)
        except Exception:
            width = sigWidths.get(sigName, 1)
            sigName = ['Mix'] + ['{}[{}:{}]'.format(sigName, j, j) for j in range(width)]
    elif isinstance(ast, vast.Cond):
        cond = getSigName(ast.cond, instance_name)
        tval = getSigName(ast.true_value, instance_name)
        fval = getSigName(ast.false_value, instance_name)
        sigName = ['Cond', cond, tval, fval]
    elif isinstance(ast, vast.Land):
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        sigName = ['And', lname, rname]
    elif isinstance(ast, vast.Lor):
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        sigName = ['Or', lname, rname]
    elif isinstance(ast, vast.Srl):
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        sigName = ['Srl', lname, rname]
    elif isinstance(ast, vast.Plus):
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        sigName = ['Plus', lname, rname]
    elif isinstance(ast, vast.Unot) or isinstance(ast, vast.Ulnot):
        rname = getSigName(ast.right, instance_name)
        sigName = ['Not', rname]
    elif isinstance(ast, vast.Or) or isinstance(ast, vast.And) or isinstance(ast, vast.Xor) or isinstance(ast,
                                                                                                          vast.Eq) or isinstance(
            ast, vast.NotEq) or isinstance(ast, vast.Sll) or isinstance(ast, vast.Minus):
        if isinstance(ast, vast.Or):
            op = 'Or'
        elif isinstance(ast, vast.And):
            op = 'And'
        elif isinstance(ast, vast.Xor):
            op = 'Xor'
        elif isinstance(ast, vast.Eq):
            op = 'Eq'
        elif isinstance(ast, vast.NotEq):
            op = 'NotEq'
        elif isinstance(ast, vast.Sll):
            op = 'Sll'
        elif isinstance(ast, vast.Minus):
            op = 'Minus'
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        sigName = [op, lname, rname]
    elif isinstance(ast, vast.IntConst):
        sigName = utils.verilogIntConstToInt(ast)
    elif isinstance(ast, vast.Concat):
        sigName = [getSigName(x, instance_name) for x in ast.list[::-1]]
    elif isinstance(ast, vast.Repeat):
        try:
            rep_count = utils.verilogIntConstToInt(ast.times)
        except Exception:
            rep_count = 1
        repeated = getSigName(ast.value, instance_name)
        sigName = []
        for _ in range(max(rep_count, 0)):
            if isinstance(repeated, list) and repeated and isinstance(repeated[0], str) and repeated[0] in (
                "Cond", "Srl", "Plus", "Or", "And", "Xor", "Eq", "NotEq", "Sll", "Not", "EqBus", "EqVec",
                "Nand", "Nor", "LutConstBit", "Times", "Minus"
            ):
                sigName.append(copy.deepcopy(repeated))
            else:
                if isinstance(repeated, list):
                    sigName.extend(copy.deepcopy(repeated))
                else:
                    sigName.append(copy.deepcopy(repeated))
    elif isinstance(ast, vast.Uand):
        operand = getSigName(ast.right, instance_name)

        def _operand_bits(expr):
            if isinstance(expr, int):
                return [expr]
            if isinstance(expr, str):
                if "[" in expr and ":" in expr and expr.endswith("]"):
                    try:
                        base = expr.split("[", 1)[0]
                        rng = expr.split("[", 1)[1].split("]")[0]
                        msb, lsb = map(int, rng.split(":"))
                        return [f"{base}[{i}:{i}]" for i in range(lsb, msb + 1)]
                    except Exception:
                        pass
                width = sigWidths.get(expr.split("[", 1)[0], 1)
                return [f"{expr.split('[', 1)[0]}[{i}:{i}]" for i in range(width)]
            if isinstance(expr, list):
                if not expr:
                    return []
                if isinstance(expr[0], str) and expr[0] in (
                    "Cond", "Srl", "Plus", "Or", "And", "Xor", "Eq", "NotEq", "Sll", "Not", "EqBus", "EqVec",
                    "Nand", "Nor", "LutConstBit", "Times", "Minus"
                ):
                    return [expr]
                out = []
                for item in expr:
                    out.extend(_operand_bits(item))
                return out
            return [expr]

        bits = _operand_bits(operand)
        if not bits:
            sigName = 0
        else:
            acc = bits[0]
            for bit in bits[1:]:
                acc = ['And', acc, bit]
            sigName = acc
    else:
        sigName = '{}.{}'.format(instance_name, ast)
        width = sigWidths.get(sigName, 1)
        sigName = '{}[{}:{}]'.format(sigName, width - 1, 0)

    return sigName


# getting the list of rhs signal names from the signal name and its low, high indices
def getRnamesExpr(rname, low, high):
    def _bits_from_signal_name(sig):
        if not isinstance(sig, str):
            return None
        if "[" in sig and ":" in sig and sig.endswith("]"):
            try:
                base = sig.split("[", 1)[0]
                rng = sig.split("[", 1)[1].split("]")[0]
                msb, lsb = map(int, rng.split(":"))
                return [f"{base}[{i}:{i}]" for i in range(lsb, msb + 1)]
            except Exception:
                return None
        width = sigWidths.get(sig, None)
        if isinstance(width, int) and width > 0:
            return [f"{sig}[{i}:{i}]" for i in range(width)]
        return None

    def _bit_at(expr, idx):
        if isinstance(expr, int):
            return (expr >> (idx - low)) & 1
        if isinstance(expr, str):
            bits = _bits_from_signal_name(expr)
            if bits is None:
                return expr
            rel = idx - low
            if 0 <= rel < len(bits):
                return bits[rel]
            return 0
        if not isinstance(expr, list) or not expr:
            return expr

        op = expr[0] if isinstance(expr[0], str) else None
        if op in ("Or", "And", "Xor", "Eq", "NotEq", "Sll", "Srl", "Plus", "Times", "Minus"):
            left = expr[1] if len(expr) > 1 else 0
            right = expr[2] if len(expr) > 2 else 0
            return [op, _bit_at(left, idx), _bit_at(right, idx)]
        if op == "Not":
            return ["Not", _bit_at(expr[1], idx)]
        if op == "Cond":
            tval = expr[2] if len(expr) > 2 else 0
            fval = expr[3] if len(expr) > 3 else 0
            return ["Cond", expr[1], _bit_at(tval, idx), _bit_at(fval, idx)]
        if op == "EqBus":
            return expr
        if op == "EqVec":
            return expr
        if op == "LutConstBit":
            return expr

        flat_bits = []
        for part in expr:
            part_bits = _bits_from_signal_name(part) if isinstance(part, str) else None
            if part_bits is not None:
                flat_bits.extend(part_bits)
            elif isinstance(part, int):
                flat_bits.append(part)
            elif isinstance(part, list):
                flat_bits.extend(_flatten_bits(part))
            else:
                flat_bits.append(part)
        rel = idx - low
        if 0 <= rel < len(flat_bits):
            return flat_bits[rel]
        return 0

    def _flatten_bits(expr):
        if isinstance(expr, int):
            return [expr]
        if isinstance(expr, str):
            bits = _bits_from_signal_name(expr)
            return bits if bits is not None else [expr]
        if not isinstance(expr, list) or not expr:
            return [expr]
        if isinstance(expr[0], str) and expr[0] in (
            "Or", "And", "Xor", "Eq", "NotEq", "Sll", "Srl", "Plus", "Times", "Minus", "Cond", "Not",
            "EqBus", "EqVec", "LutConstBit"
        ):
            return [expr]
        out = []
        for part in expr:
            out.extend(_flatten_bits(part))
        return out

    if not isinstance(rname, list):
        rbase = int(rname.split("]")[0].split(":")[1])
        return ['{}[{}:{}]'.format(rname.split('[')[0], rbase + i - low, rbase + i - low) for i in range(low, high + 1)]

    if rname and isinstance(rname[0], str) and rname[0] in (
        "Or", "And", "Xor", "Eq", "NotEq", "Sll", "Srl", "Plus", "Times", "Minus"
    ):
        return [rname[0], [_bit_at(rname[1], i) for i in range(low, high + 1)],
                [_bit_at(rname[2], i) for i in range(low, high + 1)]]
    if rname and isinstance(rname[0], str) and rname[0] == "Not":
        return ["Not", [_bit_at(rname[1], i) for i in range(low, high + 1)]]

    return [_bit_at(rname, i) for i in range(low, high + 1)]


def _collect_nonblocking_nodes(ast):
    nodes = []
    if ast is None:
        return nodes
    if isinstance(ast, vast.NonblockingSubstitution):
        nodes.append(ast)
    for child in ast.children():
        nodes.extend(_collect_nonblocking_nodes(child))
    return nodes


def get_nonblocking_clk_map(moduleAst, instance_name):
    nb_clk_map = {}
    for item in moduleAst.items:
        if not isinstance(item, vast.Always):
            continue
        sens_list = getattr(item, "sens_list", None)
        if sens_list is None:
            continue

        clk_name = None
        for sens in getattr(sens_list, "list", []):
            if (
                isinstance(sens, vast.Sens)
                and sens.type == 'posedge'
                and isinstance(sens.sig, vast.Identifier)
                and sens.sig.name == 'clk'
            ):
                clk_name = f'{instance_name}.clk[0:0]'
                break

        for nb_ast in _collect_nonblocking_nodes(getattr(item, "statement", None)):
            lhs_ast = getattr(nb_ast.left, "var", nb_ast.left)
            try:
                lhs_name = getSigName(lhs_ast, instance_name)
                lhs_base = lhs_name.rsplit('[', 1)[0]
                nb_clk_map[lhs_base] = clk_name
            except Exception:
                nb_clk_map[hash(nb_ast)] = clk_name

    return nb_clk_map


def gate_with_clk(expr, clk_name):
    """Approximate posedge-triggered updates as data gated by the clock signal."""
    if not isinstance(clk_name, str):
        return expr
    if isinstance(expr, list) and expr and expr[0] == 'LutConstBit':
        if len(expr) > 4 and expr[4] == clk_name:
            return expr
        if len(expr) == 4:
            return expr + [clk_name]
    if isinstance(expr, int):
        if expr == 0:
            return 0
        if expr == 1:
            return clk_name
        return expr
    if expr == clk_name:
        return clk_name
    if isinstance(expr, list) and len(expr) == 3 and expr[0] == 'And':
        if expr[1] == clk_name or expr[2] == clk_name:
            return expr
    return ['And', expr, clk_name]


# populates the expressions corresponding to the module/instance and all its internal modules/instances
def populateModuleExprMap(module_name, instance_name):
    global moduleInputPortListMap
    global moduleOutputPortListMap
    global moduleWireListMap
    global moduleInputPortWidthListMap
    global moduleOutputPortWidthListMap
    global moduleWireWidthListMap
    global moduleWireExprMap
    global moduleWireWidthMap
    global sigWidths
    global signalNames

    moduleAst = moduleAstMap[module_name]
    inst_list = getInstListFromAst(moduleAst)

    ins_m = ['{}.{}'.format(instance_name, port) for port in moduleInputPortListMap[module_name]]
    insWidths_m = moduleInputPortWidthListMap[module_name][:]
    outs_m = ['{}.{}'.format(instance_name, port) for port in moduleOutputPortListMap[module_name]]
    outsWidths_m = moduleOutputPortWidthListMap[module_name][:]
    wires_m = ['{}.{}'.format(instance_name, wire) for wire in moduleWireListMap[module_name]]
    wiresWidths_m = moduleWireWidthListMap[module_name][:]

    for sigName, inwid in zip(ins_m, insWidths_m):
        sigWidths[sigName] = inwid

    for sigName, outwid in zip(outs_m, outsWidths_m):
        sigWidths[sigName] = outwid

    for sigName, wirewid in zip(wires_m, wiresWidths_m):
        sigWidths[sigName] = wirewid

    nonblocking_clk_map = get_nonblocking_clk_map(moduleAst, instance_name)

    if inst_list:
        try:
            with open("debug_recurse.txt", "a") as df:
                df.write(f"Module {module_name} has instances: {[i.name for i in inst_list]}\n")
        except:
            pass
        # analyse each of the instances within this module
        for inst in inst_list:
            inst_module_name = inst.module
            inst_name = '{}.{}'.format(instance_name, inst.name)

            populateModuleInputOutputPortListMap(moduleAstMap[inst_module_name])

            ins = ['{}.{}'.format(inst_name, port) for port in moduleInputPortListMap[inst_module_name]]
            insWidths = moduleInputPortWidthListMap[inst_module_name][:]
            outs = ['{}.{}'.format(inst_name, w1) for w1 in moduleOutputPortListMap[inst_module_name]]
            outsWidths = moduleOutputPortWidthListMap[inst_module_name][:]

            portnames = [portAst.portname for portAst in inst.portlist]

            if not any(portnames):
                # assuming that the first argument is the output port (o) and the rest are inputs (i1, i2 ...)
                inst.portlist[0].portname = 'o'
                for i in range(1, len(portnames)):
                    inst.portlist[i].portname = 'i{}'.format(i)

            # populating the truth table with expressions corresponding to the instance inputs
            for x in inst.portlist:
                lname = '{}.{}'.format(inst_name, x.portname)
                if lname in ins:
                    wid = insWidths[ins.index(lname)]
                    lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)

                    for l, r in zip(lnames, rnames):
                        truthTableMap[l] = r

            # recursive call for analysing the instance (and any internal instances in it)
            populateModuleExprMap(inst_module_name, inst_name)

            # populating the truth table with expressions corresponding to the instance outputs
            for x in inst.portlist:
                lname = '{}.{}'.format(inst_name, x.portname)
                if lname in outs:
                    wid = outsWidths[outs.index(lname)]
                    lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)

                    for l, r in zip(lnames, rnames):
                        truthTableMap[r] = l

    # getting the expressions corresponding to the wires in the instance
    moduleWireExprMap[module_name], moduleWireWidthMap[module_name], modTopSortMap = generate_z3.generateModuleMaps(
        moduleAst, moduleInputPortListMap, moduleOutputPortListMap, moduleInputPortWidthListMap,
        moduleOutputPortWidthListMap, moduleWireExprMap)

    if module_name == "lfsr_counter":
        try:
            with open("debug_recurse.txt", "a") as df:
                df.write(f"AST for lfsr_counter:\n")
                if hasattr(moduleAst, 'items'):
                    for item in moduleAst.items:
                        df.write(f"  Item type: {type(item)}\n")
                        if isinstance(item, vast.Always):
                            df.write(f"    Always block sens: {item.sens_list}\n")
                            # Check statements inside
                            stmt = item.statement
                            df.write(f"    Statement: {type(stmt)}\n")
                df.write(f"modTopSortMap size: {len(modTopSortMap)}\n")
        except:
            pass

    # populating the widths of the signals in the module/instance
    for w, wid in sorted(moduleWireWidthMap[module_name].items()):
        sigName = '{}.{}'.format(instance_name, w)
        sigWidths[sigName] = wid
    astProcessed = {}

    for node in modTopSortMap:
        for (key, val) in node.incomingEdgeAstMap.items():
            for ast in val:
                if hash(ast) not in astProcessed:
                    astProcessed[hash(ast)] = True
                    if isinstance(ast, vast.Assign) or isinstance(ast, vast.NonblockingSubstitution):
                        lhsAst = getattr(ast.left, "var", ast.left)
                        rhsAst = getattr(ast.right, "var", ast.right)

                        lname = getSigName(lhsAst, instance_name)
                        rname    = getSigName(rhsAst, instance_name)

                        lnamesplit = lname.rsplit('[', 1)
                        lnameonly = lnamesplit[0]
                        lbits = lnamesplit[1].split(']')[0]
                        lbits = lbits.split(':')

                        low = int(lbits[1])
                        high = int(lbits[0])

                        cur_clk_name = None
                        if isinstance(ast, vast.NonblockingSubstitution):
                            cur_clk_name = nonblocking_clk_map.get(lnameonly)
                            if cur_clk_name is None:
                                cur_clk_name = nonblocking_clk_map.get(hash(ast))

                        if isinstance(ast, vast.NonblockingSubstitution):
                            try:
                                with open("debug_recurse.txt", "a") as df:
                                    df.write(f"Processing assign in {instance_name}: LHS={lnameonly}\n")
                            except:
                                pass
                            seqBases.add(lnameonly)

                        if isinstance(rhsAst, vast.Plus):
                            # Carry-aware approximation for additions (handles counter+1 better).
                            def _bits(expr):
                                try:
                                    return getRnamesExpr(expr, low, high)
                                except Exception:
                                    return None

                            # Identify constant operand if present
                            const_side = None
                            try:
                                if isinstance(rhsAst.left, vast.IntConst):
                                    const_side = ('left', utils.verilogIntConstToInt(rhsAst.left))
                                elif isinstance(rhsAst.right, vast.IntConst):
                                    const_side = ('right', utils.verilogIntConstToInt(rhsAst.right))
                            except Exception:
                                const_side = None

                            # Utility ops with simple simplifications
                            def _xor(a, b):
                                if isinstance(a, int) and isinstance(b, int):
                                    return a ^ b
                                if a == 0:
                                    return b
                                if b == 0:
                                    return a
                                if a == 1:
                                    return ['Not', b]
                                if b == 1:
                                    return ['Not', a]
                                return ['Xor', a, b]

                            def _and(a, b):
                                if isinstance(a, int) and isinstance(b, int):
                                    return a & b
                                if a == 0 or b == 0:
                                    return 0
                                if a == 1:
                                    return b
                                if b == 1:
                                    return a
                                return ['And', a, b]

                            def _or(a, b):
                                if isinstance(a, int) and isinstance(b, int):
                                    return a | b
                                if a == 1 or b == 1:
                                    return 1
                                if a == 0:
                                    return b
                                if b == 0:
                                    return a
                                return ['Or', a, b]

                            if const_side is not None:
                                # Full-adder per bit with a constant
                                const_val = const_side[1]
                                other_ast = rhsAst.right if const_side[0] == 'left' else rhsAst.left
                                other_name = getSigName(other_ast, instance_name)
                                other_bits = _bits(other_name)

                                carry = 0
                                for i in range(low, high + 1):
                                    abit = other_bits[1][i - low] if other_bits and len(other_bits) > 1 else 0
                                    bbit = (const_val >> (i - low)) & 1
                                    sum_no_carry = _xor(abit, bbit)
                                    sum_bit = _xor(sum_no_carry, carry)
                                    carry_out = _or(_and(abit, bbit), _or(_and(abit, carry), _and(bbit, carry)))
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(sum_bit, cur_clk_name)
                                    carry = carry_out
                            else:
                                # Fallback: bitwise XOR (original approximation)
                                if isinstance(rname, list) and len(rname) >= 3:
                                    a_bits = _bits(rname[1])
                                    b_bits = _bits(rname[2])
                                else:
                                    a_bits = _bits(rname)
                                    b_bits = None
                                for i in range(low, high + 1):
                                    abit = a_bits[1][i - low] if a_bits and len(a_bits) > 1 else 0
                                    bbit = b_bits[1][i - low] if b_bits and len(b_bits) > 1 else 0
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(['Xor', abit, bbit], cur_clk_name)

                        elif (lbits[0] == lbits[1]):
                            truthTableMap[lname] = gate_with_clk(simplify_large_const_lut_cond(
                                rname,
                                clk_name=cur_clk_name,
                            ) if isinstance(rname, list) else rname, cur_clk_name)

                        else:

                            if isinstance(rhsAst, vast.Or) or isinstance(rhsAst, vast.And) or isinstance(rhsAst,
                                                                                                         vast.Xor) or isinstance(
                                    rhsAst, vast.Eq) or isinstance(rhsAst, vast.NotEq) or isinstance(rhsAst, vast.Sll):
                                rnames = getRnamesExpr(rname, low, high)
                                for i in range(low, high + 1):
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                        [rnames[0], rnames[1][i - low], rnames[2][i - low]],
                                        cur_clk_name,
                                    )


                            elif isinstance(rhsAst, vast.IntConst):
                                # Convert Verilog literal (e.g. "8'h01") to integer
                                const_val = utils.verilogIntConstToInt(rhsAst)
                                width = high - low + 1
                                # Build a binary string of the right width
                                bitstring = format(const_val, '0{}b'.format(width))
                                bitstring = bitstring[::-1]
                                for i in range(low, high + 1):
                                    bit_idx = i - low
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                        int(bitstring[bit_idx]),
                                        cur_clk_name,
                                    )
                            elif isinstance(rhsAst, vast.Cond):
                                condName = getSigName(rhsAst.cond, instance_name)
                                tName = getSigName(rhsAst.true_value, instance_name)
                                fName = getSigName(rhsAst.false_value, instance_name)
                                #print("fName = {}".format(fName))
                                width = high - low + 1
                                #print("width = {}".format(width))

                                def _is_pure_self_shift_branch(branch_ast):
                                    # Only treat explicit self right-shifts as shift-register behavior.
                                    # Example: SHIFTReg <= SHIFTReg >> k
                                    if not isinstance(branch_ast, vast.Srl):
                                        return False
                                    left_name = getSigName(branch_ast.left, instance_name)
                                    return isinstance(left_name, str) and left_name.rsplit('[', 1)[0] == lnameonly

                                pure_self_shift_true = _is_pure_self_shift_branch(rhsAst.true_value)
                                pure_self_shift_false = _is_pure_self_shift_branch(rhsAst.false_value)

                                def _bit_at(expr_name, idx):
                                    if isinstance(expr_name, int):
                                        # extract the bit at position (idx-low) from the integer literal
                                        return (expr_name >> (idx - low)) & 1 if low is not None else (
                                                                                                                  expr_name >> idx) & 1
                                    if isinstance(expr_name, list):
                                        # Handle concatenation-like lists (no op tag): pick the appropriate element bit
                                        if expr_name and not isinstance(expr_name[0], str):
                                            pass  # unlikely pattern
                                        if expr_name and isinstance(expr_name[0], str) and expr_name[0] not in (
                                        "Cond", "Srl", "Plus"):
                                            # Treat as concatenation elements, LSB-first
                                            bit_cursor = low
                                            for elem in expr_name:
                                                if isinstance(elem, int):
                                                    if idx == bit_cursor:
                                                        return elem
                                                    bit_cursor += 1
                                                    continue
                                                if isinstance(elem, str):
                                                    if '[' in elem:
                                                        base = elem.split('[')[0]
                                                        rng = elem.split('[')[1].split(']')[0]
                                                        if ':' in rng:
                                                            msb, lsb = map(int, rng.split(':'))
                                                        else:
                                                            msb = lsb = int(rng)
                                                    else:
                                                        base = elem
                                                        lsb = 0
                                                        msb = sigWidths.get(elem, 1) - 1
                                                    width_elem = msb - lsb + 1
                                                    if bit_cursor <= idx < bit_cursor + width_elem:
                                                        sel = lsb + (idx - bit_cursor)
                                                        return f'{base}[{sel}:{sel}]'
                                                    bit_cursor += width_elem
                                                else:
                                                    bit_cursor += 1
                                            return 0
                                        # nested conditional or other expression; recurse on branches if it's a Cond
                                        if len(expr_name) == 4 and expr_name[0] == 'Cond':
                                            return ['Cond',
                                                    expr_name[1],
                                                    _bit_at(expr_name[2], idx),
                                                    _bit_at(expr_name[3], idx)]
                                        if len(expr_name) == 3 and expr_name[0] == 'Srl':
                                            base = expr_name[1]
                                            sh = expr_name[2] if isinstance(expr_name[2], int) else 0
                                            try:
                                                base_name, rng = base.split('[', 1)
                                                rng = rng.split(']')[0]
                                                if ':' in rng:
                                                    msb, lsb = map(int, rng.split(':'))
                                                else:
                                                    msb = lsb = int(rng)
                                                src_idx = idx + sh
                                                if src_idx < lsb or src_idx > msb:
                                                    return 0
                                                return f'{base_name}[{src_idx}:{src_idx}]'
                                            except Exception:
                                                return expr_name
                                        if len(expr_name) == 3 and expr_name[0] in ('Plus', 'Minus'):
                                            left = expr_name[1]
                                            right = expr_name[2]

                                            if ARITH_CARRY_LIMIT <= 0:
                                                def bit_from_name(name, bit_idx):
                                                    if isinstance(name, int):
                                                        return (name >> bit_idx) & 1
                                                    if isinstance(name, list):
                                                        return _bit_at(name, bit_idx)
                                                    try:
                                                        base = name.rsplit('[', 1)[0]
                                                        rng = name.split("[", 1)[1].split("]")[0]
                                                        if ':' in rng:
                                                            msb, lsb = map(int, rng.split(':'))
                                                        else:
                                                            msb = lsb = int(rng)
                                                        if bit_idx < lsb or bit_idx > msb:
                                                            return 0
                                                        return f'{base}[{bit_idx}:{bit_idx}]'
                                                    except Exception:
                                                        return name

                                                def _same_bus(name):
                                                    return isinstance(name, str) and name.rsplit('[', 1)[0] == lnameonly

                                                if _same_bus(left):
                                                    chosen = left
                                                elif _same_bus(right):
                                                    chosen = right
                                                else:
                                                    chosen = left
                                                return bit_from_name(chosen, idx)

                                            def _xor(a, b):
                                                if isinstance(a, int) and isinstance(b, int):
                                                    return a ^ b
                                                if a == 0:
                                                    return b
                                                if b == 0:
                                                    return a
                                                if a == 1:
                                                    return ['Not', b]
                                                if b == 1:
                                                    return ['Not', a]
                                                return ['Xor', a, b]

                                            def _and(a, b):
                                                if isinstance(a, int) and isinstance(b, int):
                                                    return a & b
                                                if a == 0 or b == 0:
                                                    return 0
                                                if a == 1:
                                                    return b
                                                if b == 1:
                                                    return a
                                                return ['And', a, b]

                                            def _or(a, b):
                                                if isinstance(a, int) and isinstance(b, int):
                                                    return a | b
                                                if a == 1 or b == 1:
                                                    return 1
                                                if a == 0:
                                                    return b
                                                if b == 0:
                                                    return a
                                                return ['Or', a, b]

                                            def _not(a):
                                                if isinstance(a, int):
                                                    return 1 - a
                                                return ['Not', a]

                                            carry = 1 if expr_name[0] == 'Minus' else 0
                                            sum_bit = 0
                                            start_idx = max(low, idx - ARITH_CARRY_LIMIT + 1)
                                            for bit_idx in range(start_idx, idx + 1):
                                                abit = _bit_at(left, bit_idx)
                                                bbit = _bit_at(right, bit_idx)
                                                if expr_name[0] == 'Minus':
                                                    bbit = _not(bbit)
                                                sum_no_carry = _xor(abit, bbit)
                                                sum_bit = _xor(sum_no_carry, carry)
                                                carry = _or(_and(abit, bbit), _or(_and(abit, carry), _and(bbit, carry)))
                                            return sum_bit
                                        return expr_name
                                    try:
                                        base = expr_name.rsplit('[', 1)[0]
                                        base_idx = int(expr_name.split("]")[0].split(":")[1])
                                        return '{}[{}:{}]'.format(base, base_idx + idx - low, base_idx + idx - low)
                                    except (IndexError, ValueError, AttributeError):
                                        return expr_name

                                # Build bit-level expressions first
                                bit_exprs = {}
                                for i in range(low, high + 1):
                                    tbit = _bit_at(tName, i)
                                    fbit = _bit_at(fName, i)
                                    # simplify pattern: Cond(Not condName, X, 0) -> X
                                    if isinstance(fbit, list) and len(fbit) == 4 and fbit[0] == 'Cond' and isinstance(
                                            fbit[1], list):
                                        inner_cond = fbit[1]
                                        if inner_cond == ['Not', condName] and fbit[3] == 0:
                                            fbit = fbit[2]
                                    bit_exprs[i] = simplify_large_const_lut_cond(
                                        ['Cond', condName, tbit, fbit],
                                        target_bit_idx=i,
                                        clk_name=cur_clk_name,
                                    )

                                def _parse_self_ref(expr):
                                    """Return bit index if expr is a self-reference to lnameonly[idx:idx]."""
                                    if isinstance(expr, str) and expr.startswith(lnameonly + "["):
                                        try:
                                            idx_str = expr.split("[", 1)[1].split(":", 1)[0]
                                            return int(idx_str)
                                        except (IndexError, ValueError):
                                            return None
                                    return None

                                def _normalize_shift(ref_idx, cur_idx):
                                    """
                                    Return (delta, wrap) where delta is the preferred step (positive=right,
                                    negative=left) and wrap indicates modulo chaining (rotate).
                                    """
                                    raw = ref_idx - cur_idx
                                    k_mod = (ref_idx - cur_idx) % width
                                    if k_mod == 0:
                                        return 0, False
                                    alt = k_mod if k_mod <= width // 2 else k_mod - width
                                    wrap = (alt != raw)
                                    return alt, wrap

                                def _build_shift_chain(idx, shift_delta, depth, wrap, data_expr):
                                    """
                                    Approximate shift/rotate over time as a mix of source bits.
                                    If the shift walks off the bus (non-rotating), terminate with 0.
                                    """
                                    depth = max(1, depth)
                                    key_bits = []
                                    for step in range(depth):
                                        bit_idx = idx + step * shift_delta
                                        if wrap:
                                            span = width
                                            bit_idx = ((bit_idx - low) % span) + low
                                        elif bit_idx < low or bit_idx > high:
                                            key_bits.append(0)
                                            break
                                        key_bits.append(_bit_at(data_expr, bit_idx))
                                    if not key_bits:
                                        return 0
                                    return ['Mix'] + key_bits

                                def _expand_bus_bits(expr_name):
                                    if isinstance(expr_name, int):
                                        # constant: expand to width bits
                                        bits = []
                                        for b in range(width):
                                            bits.append((expr_name >> b) & 1)
                                        return bits
                                    if isinstance(expr_name, list):
                                        # already a composed expression; treat as-is
                                        return [expr_name]
                                    if not isinstance(expr_name, str):
                                        return [expr_name]
                                    try:
                                        base = expr_name.rsplit('[', 1)[0]
                                        rng = expr_name.split("[", 1)[1].split("]")[0]
                                        if ':' in rng:
                                            msb, lsb = map(int, rng.split(':'))
                                        else:
                                            msb = lsb = int(rng)
                                    except Exception:
                                        base = expr_name
                                        msb = sigWidths.get(base, width) - 1
                                        lsb = 0
                                    return [f'{base}[{j}:{j}]' for j in range(lsb, msb + 1)]

                                depth_limit = min(SHIFT_UNROLL_LIMIT, high - low + 1)
                                for i in range(low, high + 1):
                                    expr = bit_exprs.get(i)
                                    if isinstance(expr, list) and expr[0] == 'Cond':
                                        ref_idx = _parse_self_ref(expr[3])
                                        data_expr = tName
                                        if ref_idx is None:
                                            ref_idx = _parse_self_ref(expr[2])
                                            if ref_idx is not None:
                                                data_expr = fName
                                        # Only treat as pure shift if the data expression is the same bus (true self-shift).
                                        if ref_idx is not None and isinstance(data_expr, str) and data_expr.startswith(
                                                lnameonly):
                                            shift_delta, wrap = _normalize_shift(ref_idx, i)
                                            if shift_delta != 0:
                                                chain_expr = _build_shift_chain(i, shift_delta, depth_limit, wrap,
                                                                                data_expr)
                                                truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                                    chain_expr,
                                                    cur_clk_name,
                                                )
                                                continue
                                        # Load-then-shift modeling: if one branch is self-shift and the other
                                        # branch is an external bus (e.g., key), approximate as a Mix over that bus.
                                        if ref_idx is not None and isinstance(data_expr, str) and not data_expr.startswith(
                                                lnameonly):
                                            mix_bits = _expand_bus_bits(data_expr)
                                            truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                                ['Mix'] + mix_bits,
                                                cur_clk_name,
                                            )
                                            continue
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(expr, cur_clk_name)

                            elif isinstance(rhsAst, vast.Srl):
                                # logical right shift by constant
                                try:
                                    shift_amt = utils.verilogIntConstToInt(rhsAst.right)
                                except Exception:
                                    shift_amt = 0
                                for i in range(low, high + 1):
                                    src_idx = i + shift_amt
                                    if src_idx <= high:
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                            '{}[{}:{}]'.format(lnameonly, src_idx, src_idx),
                                            cur_clk_name,
                                        )
                                    else:
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(0, cur_clk_name)
                            elif isinstance(rhsAst, vast.Plus) or isinstance(rhsAst, vast.Minus):
                                def _bits(expr):
                                    try:
                                        return getRnamesExpr(expr, low, high)
                                    except Exception:
                                        return None

                                left_name = getSigName(rhsAst.left, instance_name)
                                right_name = getSigName(rhsAst.right, instance_name)
                                left_bits = _bits(left_name)
                                right_bits = _bits(right_name)

                                if ARITH_CARRY_LIMIT <= 0:
                                    def _bit_passthrough(name, bits, i):
                                        if isinstance(name, int):
                                            return (name >> (i - low)) & 1
                                        if (
                                            isinstance(bits, list)
                                            and bits
                                            and isinstance(bits[0], str)
                                            and bits[0] in (
                                                "Or", "And", "Xor", "Eq", "NotEq", "Sll", "Srl",
                                                "Plus", "Times", "Minus"
                                            )
                                        ):
                                            return bits[1][i - low]
                                        if isinstance(bits, list) and 0 <= i - low < len(bits):
                                            return bits[i - low]
                                        return 0

                                    def _same_bus(name):
                                        return isinstance(name, str) and name.rsplit('[', 1)[0] == lnameonly

                                    if _same_bus(left_name):
                                        chosen_name, chosen_bits = left_name, left_bits
                                    elif _same_bus(right_name):
                                        chosen_name, chosen_bits = right_name, right_bits
                                    else:
                                        chosen_name, chosen_bits = left_name, left_bits

                                    for i in range(low, high + 1):
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                            _bit_passthrough(chosen_name, chosen_bits, i),
                                            cur_clk_name,
                                        )
                                    continue

                                def _xor(a, b):
                                    if isinstance(a, int) and isinstance(b, int):
                                        return a ^ b
                                    if a == 0:
                                        return b
                                    if b == 0:
                                        return a
                                    if a == 1:
                                        return ['Not', b]
                                    if b == 1:
                                        return ['Not', a]
                                    return ['Xor', a, b]

                                def _and(a, b):
                                    if isinstance(a, int) and isinstance(b, int):
                                        return a & b
                                    if a == 0 or b == 0:
                                        return 0
                                    if a == 1:
                                        return b
                                    if b == 1:
                                        return a
                                    return ['And', a, b]

                                def _or(a, b):
                                    if isinstance(a, int) and isinstance(b, int):
                                        return a | b
                                    if a == 1 or b == 1:
                                        return 1
                                    if a == 0:
                                        return b
                                    if b == 0:
                                        return a
                                    return ['Or', a, b]

                                def _not(a):
                                    if isinstance(a, int):
                                        return 1 - a
                                    return ['Not', a]

                                def _bit(name, bits, i):
                                    if isinstance(name, int):
                                        return (name >> (i - low)) & 1
                                    if bits:
                                        if (
                                            isinstance(bits, list)
                                            and bits
                                            and isinstance(bits[0], str)
                                            and bits[0] in (
                                                "Or", "And", "Xor", "Eq", "NotEq", "Sll", "Srl",
                                                "Plus", "Times", "Minus"
                                            )
                                        ):
                                            return [bits[0], bits[1][i - low], bits[2][i - low]]
                                        return bits[i - low]
                                    return 0

                                for i in range(low, high + 1):
                                    carry = 1 if isinstance(rhsAst, vast.Minus) else 0
                                    sum_bit = 0
                                    start_idx = max(low, i - ARITH_CARRY_LIMIT + 1)
                                    for bit_idx in range(start_idx, i + 1):
                                        abit = _bit(left_name, left_bits, bit_idx)
                                        bbit = _bit(right_name, right_bits, bit_idx)
                                        if isinstance(rhsAst, vast.Minus):
                                            bbit = _not(bbit)
                                        sum_no_carry = _xor(abit, bbit)
                                        sum_bit = _xor(sum_no_carry, carry)
                                        carry = _or(_and(abit, bbit), _or(_and(abit, carry), _and(bbit, carry)))
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                        sum_bit,
                                        cur_clk_name,
                                    )
                            else:
                                # Check if rname is a list (concatenation or operation result)
                                if isinstance(rname, list):
                                    if rname and rname[0] == 'Mix':
                                        # Dynamic index select already represented as Mix; reuse it for each dest bit.
                                        for i in range(low, high + 1):
                                            truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(rname, cur_clk_name)
                                    else:
                                        # Handle concatenation: rname is [signal1, signal2, signal3, ...]
                                        # Bits are mapped left-to-right in the list (right-to-left in Verilog)
                                        bit_index = low
                                        for concat_elem in rname:
                                            if isinstance(concat_elem, str):
                                                if '[' in concat_elem:
                                                    elem_base = concat_elem.split('[')[0]
                                                    elem_range = concat_elem.split('[')[1].split(']')[0]
                                                    if ':' in elem_range:
                                                        elem_msb, elem_lsb = map(int, elem_range.split(':'))
                                                        elem_width = elem_msb - elem_lsb + 1
                                                    else:
                                                        elem_lsb = int(elem_range)
                                                        elem_width = 1
                                                else:
                                                    elem_base = concat_elem
                                                    elem_width = sigWidths.get(concat_elem, 1)
                                                    elem_lsb = 0
                                                for j in range(elem_width):
                                                    if bit_index <= high:
                                                        src_bit = '{}[{}:{}]'.format(elem_base, elem_lsb + j,
                                                                                     elem_lsb + j)
                                                        dest_bit = '{}[{}:{}]'.format(lnameonly, bit_index, bit_index)
                                                        truthTableMap[dest_bit] = gate_with_clk(src_bit, cur_clk_name)
                                                        bit_index += 1
                                            elif isinstance(concat_elem, int):
                                                if bit_index <= high:
                                                    truthTableMap['{}[{}:{}]'.format(lnameonly, bit_index,
                                                                                     bit_index)] = gate_with_clk(concat_elem, cur_clk_name)
                                                    bit_index += 1
                                else:
                                    # Handle simple signal assignment
                                    try:
                                        rnameonly = rname.rsplit('[', 1)[0]
                                        rbase = int(rname.split("]")[0].split(":")[1])
                                        for i in range(low, high + 1):
                                            truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = gate_with_clk(
                                                '{}[{}:{}]'.format(rnameonly, rbase + i - low, rbase + i - low),
                                                cur_clk_name,
                                            )
                                    except (IndexError, ValueError):
                                        # Handle malformed signal names gracefully
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, low, high)] = gate_with_clk(
                                            rname,
                                            cur_clk_name,
                                        )
                    elif isinstance(ast, vast.Instance):
                        pass
                    else:
                        assert (False)


# gets the names of all the signals within a module/instance, including internal modules/instances
def getInternalSignalNames(module_name, instance_name):
    global moduleInputPortListMap
    global moduleOutputPortListMap
    global moduleInputPortWidthListMap
    global moduleOutputPortWidthListMap
    global moduleWireExprMap
    global moduleWireWidthMap
    global sigWidths
    global signalNames

    moduleAst = moduleAstMap[module_name]
    inst_list = getInstListFromAst(moduleAst)

    if inst_list:
        for inst in inst_list:
            inst_module_name = inst.module
            inst_name = '{}.{}'.format(instance_name, inst.name)

            ins = ['{}.{}'.format(inst_name, port) for port in moduleInputPortListMap[inst_module_name]]
            insWidths = moduleInputPortWidthListMap[inst_module_name][:]
            outs = ['{}.{}'.format(inst_name, w1) for w1 in moduleOutputPortListMap[inst_module_name]]
            outsWidths = moduleOutputPortWidthListMap[inst_module_name][:]

            portnames = [portAst.portname for portAst in inst.portlist]

            for x in inst.portlist:
                lname = '{}.{}'.format(inst_name, x.portname)
                if lname in ins:
                    wid = insWidths[ins.index(lname)]
                    lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)

                    for signalName in rnames:
                        signalNames.add(signalName)

                    for signalName in lnames:
                        signalNames.add(signalName)

            getInternalSignalNames(inst_module_name, inst_name)

            for x in inst.portlist:
                lname = '{}.{}'.format(inst_name, x.portname)
                if lname in outs:
                    wid = outsWidths[outs.index(lname)]
                    lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)

                    for signalName in lnames:
                        signalNames.add(signalName)

                    for signalName in rnames:
                        signalNames.add(signalName)

            wires = ['{}.{}'.format(inst_name, w1) for w1 in moduleWireExprMap[inst_module_name]]
            wiresWidths = [moduleWireWidthMap[inst_module_name][w1] for w1 in moduleWireExprMap[inst_module_name]]
            if wires:
                for i in range(len(wires)):
                    sig = wires[i]
                    wid = wiresWidths[i]
                    for j in range(wid):
                        signalName = '{}[{}:{}]'.format(sig, j, j)
                        signalNames.add(signalName)

    wires = ['{}.{}'.format(instance_name, w1) for w1 in moduleWireExprMap[module_name]]
    wiresWidths = [moduleWireWidthMap[module_name][w1] for w1 in moduleWireExprMap[module_name]]
    if wires:
        for i in range(len(wires)):
            sig = wires[i]
            wid = wiresWidths[i]
            for j in range(wid):
                signalName = '{}[{}:{}]'.format(sig, j, j)
                signalNames.add(signalName)


# Build a time-unrolled truth table map.
# Only true loop bases are unrolled by width; dependents inherit width from
# loop base(s) they transitively depend on. H is used only as fallback.
def build_time_unrolled_truth_table(truthTableMap, UNROLL_DEPTH):
    def _base(sig):
        if not isinstance(sig, str):
            return None
        return sig.split("[", 1)[0] if "[" in sig else sig

    def _expr_refs_base(expr, base):
        if isinstance(expr, str):
            return _base(expr) == base
        if isinstance(expr, list):
            return any(_expr_refs_base(e, base) for e in expr)
        return False

    loop_bases = {_base(k) for k, v in truthTableMap.items() if _expr_refs_base(v, _base(k))}
    seq_bases = set(seqBases)

    def _is_seq_base(base_name):
        if base_name in seq_bases:
            return True
        leaf = base_name.split('.')[-1]
        for sb in seq_bases:
            if sb == base_name: return True
            if sb.split('.')[-1] == leaf: return True
        return False

    # Only self-referential loop bases should drive temporal unrolling.
    loop_root_bases = set(loop_bases)
    loop_keys = {k for k in truthTableMap.keys() if _base(k) in loop_root_bases}

    # keys that depend on loop/seq bases (fanout aliases)
    def _expr_bases(expr):
        if isinstance(expr, str):
            b = _base(expr)
            return {b} if b else set()
        if isinstance(expr, list):
            out = set()
            for e in expr[1:]:
                out |= _expr_bases(e)
            return out
        return set()

    # compute transitive dependency on loop/seq bases (e.g., counter -> lfsr -> lfsr_stream)
    base_deps = {}
    for k, v in truthTableMap.items():
        bk = _base(k)
        if not bk:
            continue
        base_deps.setdefault(bk, set()).update(_expr_bases(v))

    # Track which loop roots each base depends on transitively.
    roots_by_base = {bk: set() for bk in base_deps.keys()}
    for rb in loop_root_bases:
        roots_by_base.setdefault(rb, set()).add(rb)

    changed = True
    while changed:
        changed = False
        for bk, deps in base_deps.items():
            roots = roots_by_base.setdefault(bk, set())
            new_roots = set()
            for dep in deps:
                new_roots |= roots_by_base.get(dep, set())
            if not new_roots.issubset(roots):
                roots |= new_roots
                changed = True

    reach = {bk for bk, roots in roots_by_base.items() if roots} | set(loop_root_bases)
    dep_keys = {k for k in truthTableMap.keys() if _base(k) in reach and _base(k) not in loop_root_bases}

    def _infer_width_from_keys(base_name):
        lo = None
        hi = None
        prefix = f"{base_name}["
        for k in truthTableMap.keys():
            if not isinstance(k, str) or not k.startswith(prefix):
                continue
            try:
                idx = int(k.split("[", 1)[1].split(":", 1)[0])
            except Exception:
                continue
            lo = idx if lo is None else min(lo, idx)
            hi = idx if hi is None else max(hi, idx)
        if lo is None or hi is None:
            return None
        return hi - lo + 1

    def _width_for_base(base_name):
        w = sigWidths.get(base_name, None)
        if isinstance(w, int) and w > 0:
            return w
        inferred = _infer_width_from_keys(base_name)
        if isinstance(inferred, int) and inferred > 0:
            return inferred
        return UNROLL_DEPTH if isinstance(UNROLL_DEPTH, int) and UNROLL_DEPTH > 0 else 1

    root_width = {rb: _width_for_base(rb) for rb in loop_root_bases}
    base_unroll_depth = {}
    for bk in reach:
        #print("bk ",bk)
        roots = roots_by_base.get(bk, set())
        if roots:
            #base_unroll_depth[bk] = max(root_width.get(r, _width_for_base(r)) for r in roots)
            base_unroll_depth[bk] = max(root_width.get(r, _width_for_base(r)) for r in root_width)
        else:
            base_unroll_depth[bk] = _width_for_base(bk)

    def _rewrite(expr, t):
        if isinstance(expr, int):
            return expr
        if isinstance(expr, str):
            b = _base(expr)
            return f"{expr}@{t}" if b in reach else expr
        if isinstance(expr, list):
            return [expr[0]] + [_rewrite(e, t) for e in expr[1:]]
        return expr

    def _expand_eq(expr):
        if not isinstance(expr, list):
            return expr
        op = expr[0]
        if op == "Eq":
            a, b = expr[1], expr[2]

            def _bits(x):
                if isinstance(x, str) and "[" in x and ":" in x and x.endswith("]"):
                    try:
                        base = x.split("[", 1)[0]
                        rng = x.split("[", 1)[1].split("]")[0]
                        msb, lsb = map(int, rng.split(":"))
                        return [f"{base}[{i}:{i}]" for i in range(lsb, msb + 1)]
                    except Exception:
                        return None
                return None

            abits = _bits(a)
            bbits = _bits(b)
            if abits and bbits and len(abits) == len(bbits):
                return ["EqBus", a, b, 0.1]
            elif abits and isinstance(b, int):
                bits = [(b >> i) & 1 for i in range(len(abits))]
                return ["EqBus", a, b, 0.1]
            else:
                return expr
        return [expr[0]] + [_expand_eq(e) for e in expr[1:]]

    def _simplify(expr):
        if isinstance(expr, list) and expr:
            if expr[0] == "Mix":
                parts = expr[1:]
                if all(isinstance(p, int) for p in parts):
                    if len(set(parts)) == 1:
                        return parts[0]
                    return sum(parts) / len(parts) if parts else 0
            return [expr[0]] + [_simplify(e) for e in expr[1:]]
        return expr

    unrolled = dict(truthTableMap)  # keep originals for non-looped signals
    sigs = set()

    for key, expr in truthTableMap.items():
        base = _base(key)
        if base in loop_root_bases and key in loop_keys:
            # For loop roots, unroll one extra step: width + 1.
            local_h = base_unroll_depth.get(base, _width_for_base(base)) + 1
            for t in range(local_h):
                lhs = f"{key}@{t + 1}"
                rhs = _rewrite(expr, t)
                rhs = _expand_eq(rhs)
                unrolled[lhs] = _simplify(rhs)
                sigs.add(lhs)
        elif key in dep_keys:
            local_h = base_unroll_depth.get(base, _width_for_base(base))
            if _is_seq_base(base):
                # Sequential assignment (`<=`) advances state: key@{t+1} from RHS@t.
                # Do not define key@0 from RHS; keep @0 as a root when needed.
                for t in range(local_h):
                    lhs = f"{key}@{t + 1}"
                    rhs = _rewrite(expr, t)
                    rhs = _expand_eq(rhs)
                    unrolled[lhs] = _simplify(rhs)
                    sigs.add(lhs)
            else:
                # Combinational assignment (`assign` / blocking logic) is same-cycle.
                for t in range(local_h + 1):
                    lhs = f"{key}@{t}"
                    rhs = _rewrite(expr, t)
                    rhs = _expand_eq(rhs)
                    unrolled[lhs] = _simplify(rhs)
                    sigs.add(lhs)
        else:
            lhs = key
            rhs = _expand_eq(expr)
            unrolled[lhs] = _simplify(rhs)
            sigs.add(lhs)

    # Ensure time-0 nodes exist for loop/seq bases.
    # Important: do NOT create alias edges `key@0 -> key`; keeping @0 with no
    # truth-table expression makes it an input-like root (no parents).
    for key in truthTableMap.keys():
        base = _base(key)
        if base in reach:
            t0 = f"{key}@0"
            if t0 not in unrolled:
                sigs.add(t0)
    return unrolled, sigs


# Normalize Eq on buses to EqBus across the truth table (non-unrolled case).
def normalize_eqbus(truthTableMap):
    def _bits(bus):
        if isinstance(bus, str) and "[" in bus and ":" in bus and bus.endswith("]"):
            try:
                base = bus.split("[", 1)[0]
                rng = bus.split("[", 1)[1].split("]")[0]
                msb, lsb = map(int, rng.split(":"))
                return [f"{base}[{i}:{i}]" for i in range(lsb, msb + 1)]
            except Exception:
                return None
        return None

    def _rewrite(expr):
        if not isinstance(expr, list):
            return expr
        op = expr[0]
        if op == "Eq":
            a = expr[1] if len(expr) > 1 else None
            b = expr[2] if len(expr) > 2 else None
            abits = _bits(a)
            bbits = _bits(b)
            if abits and bbits and len(abits) == len(bbits):
                return ["EqBus", a, b, 0.1]
            if abits and isinstance(b, int):
                return ["EqBus", a, b, 0.1]
            if bbits and isinstance(a, int):
                return ["EqBus", b, a, 0.1]
        return [expr[0]] + [_rewrite(e) for e in expr[1:]]

    for k, v in list(truthTableMap.items()):
        if isinstance(v, list):
            truthTableMap[k] = _rewrite(v)


# main function to extract the sub-circuit of the design which is influenced by the reference signal bits
def subCircuitExtract(input_file_path, top_module_name, ref_module_name, ref_instance_name, ref_sig_bit_names):
    global truthTableMap
    global signalNames
    global sigWidths
    global moduleAstMap
    global moduleInputPortListMap
    global moduleOutputPortListMap
    global moduleInputPortWidthListMap
    global moduleOutputPortWidthListMap
    global moduleWireExprMap
    global moduleWireWidthMap
    global instPortInputsMap
    global instPortOutputsMap
    global seqBases

    # reset global state for a fresh run
    truthTableMap = {}
    signalNames = set(ref_sig_bit_names[:])  # seed with refs so forward tracing starts non-empty
    sigWidths = {}
    moduleAstMap = {}
    moduleInputPortListMap = {}
    moduleOutputPortListMap = {}
    moduleInputPortWidthListMap = {}
    moduleOutputPortWidthListMap = {}
    moduleWireExprMap = {}
    moduleWireWidthMap = {}
    instPortInputsMap = {}
    instPortOutputsMap = {}
    seqBases = set()

    populateModuleAstMap(input_file_path)
    populateModuleInputOutputPortListMap(moduleAstMap[top_module_name])

    print()
    print("Populate module expressions starting...")
    pme_s = time.time()

    populateModuleExprMap(top_module_name, top_module_name)

    pme_e = time.time()
    print("Ended... {:.4f}s".format(pme_e - pme_s))
    print()

    print("Subcircuit extraction starting...")
    pse_s = time.time()

    extractSubCircuit(ref_module_name, ref_instance_name, ref_sig_bit_names)

    pse_e = time.time()
    print("Ended... {:.4f}s".format(pse_e - pse_s))
    print()

    inputNames = ['{}.{}'.format(top_module_name, port) for port in moduleInputPortListMap[top_module_name]]
    inputWidths = moduleInputPortWidthListMap[top_module_name][:]

    # expose all signals in the truth table to avoid pruning unreachable loops (e.g., LFSRs)
    signalNames = set(truthTableMap.keys()) | signalNames

    # ensure Eq on buses is normalized to EqBus in non-unrolled case
    normalize_eqbus(truthTableMap)

    return inputNames, inputWidths, signalNames, sigWidths, truthTableMap

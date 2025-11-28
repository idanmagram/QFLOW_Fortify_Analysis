# This file has been taken from the earlier SOLOMON project, and modified for this project.
# This file contains the functions to parse a Verilogfile, and populate various mappings
# with the details of the module under consideration.

import time
import copy
import pyverilog.vparser.ast as vast
from pyverilog.vparser.parser import parse
import utils
import generate_z3

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

# parses the input Verilog file (along with standard module definitions)
def populateModuleAstMap(file_path):
    global moduleAstMap

    print("Parsing...")
    a = time.time()
    #ast, directives = parse([file_path, 'std_cell_lib/std_gates.v', 'std_cell_lib/std_modules.v'])
    #ast, directives = parse([file_path, 'std_cell_lib/std_gates.v', 'std_cell_lib/std_modules.v', '../verilog_files/AES-T100/src/TjIn/aes_128.v',
    #                         '../verilog_files/AES-T100/src/TjIn/lfsr.v', '../verilog_files/AES-T100/src/TjIn/round.v', '../verilog_files/AES-T100/src/TjIn/table.v',
    #                         '../verilog_files/AES-T100/src/TjIn/test_aes_128.v', '../verilog_files/AES-T100/src/TjIn/TSC.v'])
    ast, directives = parse([file_path, 'std_cell_lib/std_gates.v', 'std_cell_lib/std_modules.v'])
    '''
    ast, directives = parse([file_path, '../verilog_files/AES-T100/src/TjIn/aes_128.v',
                             '../verilog_files/AES-T100/src/TjIn/lfsr.v', '../verilog_files/AES-T100/src/TjIn/round.v',
                             '../verilog_files/AES-T100/src/TjIn/table.v',
                             '../verilog_files/AES-T100/src/TjIn/test_aes_128.v',
                             '../verilog_files/AES-T100/src/TjIn/TSC.v'])
    '''
    b = time.time()

    for item in ast.description.definitions:
        moduleAstMap[item.name] = item

    print("Parsing completed in {:.4f}s".format(b - a))
from pyverilog.vparser import ast as vast

def _width_of_decl_width(w):
    # w is vast.Width or None
    if w is None:
        return 1
    # msb/lsb are IntConst or simple expressions; use your helper
    msb = utils.verilogIntConstToInt(w.msb)
    lsb = utils.verilogIntConstToInt(w.lsb)
    return abs(int(msb) - int(lsb)) + 1

# creates mappings between modules and their input ports & widths, output ports & widths, wires
def populateModuleInputOutputPortListMap(moduleAst):
    module_name = moduleAst.name

    # reset maps for this module
    moduleInputPortListMap[module_name]       = []
    moduleInputPortWidthListMap[module_name]  = []
    moduleOutputPortListMap[module_name]      = []
    moduleOutputPortWidthListMap[module_name] = []
    moduleWireListMap[module_name]            = []
    moduleWireWidthListMap[module_name]       = []

    # --- 1) First pass: ANSI-style ports from header (Ioport) ---
    if moduleAst.portlist is not None:
        for p in moduleAst.portlist.ports:
            if isinstance(p, vast.Ioport):
                decl = p.first  # Input/Output/Inout
                name = decl.name
                wid  = _width_of_decl_width(decl.width)
                if isinstance(decl, vast.Input):
                    moduleInputPortListMap[module_name].append(name)
                    moduleInputPortWidthListMap[module_name].append(wid)
                elif isinstance(decl, vast.Output):
                    moduleOutputPortListMap[module_name].append(name)
                    moduleOutputPortWidthListMap[module_name].append(wid)
                elif isinstance(decl, vast.Inout):
                    # If you need inouts, add a parallel map; for now treat as both sides or skip
                    pass  # add handling if your flow supports inouts

    # --- 2) Second pass: scan Decl to (a) resolve non-ANSI ports and (b) collect internals ---
    # For non-ANSI, Input/Output appear in Decl items, and internal nets are Wire/Reg.
    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Decl):
            for varAst in itemAst.list:
                # Ports (non-ANSI header)
                if isinstance(varAst, vast.Input):
                    name = varAst.name
                    wid  = _width_of_decl_width(varAst.width)
                    if name not in moduleInputPortListMap[module_name]:
                        moduleInputPortListMap[module_name].append(name)
                        moduleInputPortWidthListMap[module_name].append(wid)
                elif isinstance(varAst, vast.Output):
                    name = varAst.name
                    wid  = _width_of_decl_width(varAst.width)
                    if name not in moduleOutputPortListMap[module_name]:
                        moduleOutputPortListMap[module_name].append(name)
                        moduleOutputPortWidthListMap[module_name].append(wid)

                # Internal nets
                elif isinstance(varAst, vast.Wire):
                    name = varAst.name
                    wid  = _width_of_decl_width(varAst.width)
                    moduleWireListMap[module_name].append(name)
                    moduleWireWidthListMap[module_name].append(wid)

                elif isinstance(varAst, vast.Reg):
                    # Reg is NOT a port; it’s an internal net (or an output reg already
                    # covered by the Output decl). Put in wires list so widths are known.
                    name = varAst.name
                    wid  = _width_of_decl_width(varAst.width)
                    moduleWireListMap[module_name].append(name)
                    moduleWireWidthListMap[module_name].append(wid)


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
    if isinstance(x, vast.Partselect):
        rname = '{}.{}'.format(instance_name, x.var.name)
        lsb = utils.verilogIntConstToInt(x.lsb)
        msb = utils.verilogIntConstToInt(x.msb)
        rnames = ['{}[{}:{}]'.format(rname, j, j) for j in range(lsb, msb + 1)]
    elif isinstance(x, vast.Pointer):
        rname = '{}.{}'.format(instance_name, x.var.name)
        ptr = utils.verilogIntConstToInt(x.ptr)
        signalName = '{}[{}:{}]'.format(rname, ptr, ptr)
        rnames = [signalName]
    elif isinstance(x, vast.Concat):
        rnames = [getSigName(y, instance_name) for y in x.list[::-1]]
    elif isinstance(x, vast.IntConst):
        rnames = [utils.verilogIntConstToInt(x)]
    else:
        rname = '{}.{}'.format(instance_name, x)
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

    # NEW: harvest behavioral vector assignments as combinational (no unrolling)
    _harvest_behavioral_comb_no_unroll(moduleAst, instance_name)

    # populate the input/output port maps for all the instances in the module
    for inst in inst_list:
        populateInstPortInputOutputMap(inst, instance_name)

    # the forward set contains the set of signals we want to trace forward
    # initialise this set with the reference signal bits
    forward = set(ref_sig_bit_names[:])

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


import copy
import pyverilog.vparser.ast as vast
import utils

# --- helper: safe width computation for RHS expressions and concats ---
def bitwidth_of(sig):
    # string like "inst.sig[msb:lsb]" or "inst.sig[3:3]"
    if isinstance(sig, str):
        if "[" in sig and ":" in sig:
            try:
                msb, lsb = sig.split("[",1)[1].split("]")[0].split(":")
                return abs(int(msb) - int(lsb)) + 1
            except Exception:
                return 1
        # single-bit pointer like inst.sig[3]
        if "[" in sig:
            return 1
        base = sig.split("[",1)[0]
        return sigWidths.get(base, 1)

    # integer literal
    if isinstance(sig, int):
        return 1

    # operations / tagged structures
    if isinstance(sig, list) and sig:
        op = sig[0]
        if op in ("Or","And","Xor","Eq","NotEq","Sll"):
            return max(bitwidth_of(sig[1]), bitwidth_of(sig[2]))
        if op == "Not":
            return bitwidth_of(sig[1])
        if op == "Concat":
            parts = sig[1]
            return sum(bitwidth_of(p) for p in parts)

        # if it’s some other list, be defensive
        return sum(bitwidth_of(p) for p in sig[1:]) or 0

    # unknown
    return 0


# constructs the complete signal name(s) from the ast type and instance name
def getSigName(ast, instance_name):
    # NEW: if it's already a resolved name/structure, return it as-is
    if isinstance(ast, (str, int, list)):
        return ast

    if isinstance(ast, vast.Identifier):
        sigName = f'{instance_name}.{ast.name}'
        width = sigWidths.get(sigName)
        if width is None:
            # fall back to full-bus unknown => 1-bit, or raise with context
            # raise KeyError(f"Width unknown for {sigName}")
            width = 1
        return f'{sigName}[{width-1}:0]'

    elif isinstance(ast, vast.Partselect):
        msb = getattr(ast.msb, 'value', ast.msb)
        lsb = getattr(ast.lsb, 'value', ast.lsb)
        return f'{instance_name}.{ast.var.name}[{msb}:{lsb}]'

    elif isinstance(ast, vast.Pointer):
        ptr = getattr(ast.ptr, 'value', ast.ptr)
        return f'{instance_name}.{ast.var.name}[{ptr}:{ptr}]'

    elif isinstance(ast, vast.Unot):
        rname = getSigName(ast.right, instance_name)
        return ['Not', rname]

    elif isinstance(ast, (vast.Or, vast.And, vast.Xor, vast.Eq, vast.NotEq, vast.Sll)):
        op = {vast.Or:'Or', vast.And:'And', vast.Xor:'Xor',
              vast.Eq:'Eq', vast.NotEq:'NotEq', vast.Sll:'Sll'}[type(ast)]
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        return [op, lname, rname]

    elif isinstance(ast, vast.IntConst):
        return utils.verilogIntConstToInt(ast)

    elif isinstance(ast, vast.Concat):
        parts = [getSigName(x, instance_name) for x in ast.list]
        return ['Concat', parts]

    else:
        # Fallback: treat as a full-bus identifier if possible
        sigName = f'{instance_name}.{ast}'
        width = sigWidths.get(sigName, 1)
        return f'{sigName}[{width-1}:0]'



    def qualify(name):
        """Prefix with instance_name unless it already looks hierarchical or is already qualified."""
        base = name.split("[", 1)[0]
        if base.startswith(instance_name + "."):
            return name
        if "." in base:
            return name
        return f"{instance_name}.{name}"

    def width_of(qname):
        """Get width for a qualified name; try a few candidates safely."""
        base = qname.split("[", 1)[0]
        # try exact base
        w = sigWidths.get(base)
        if w is not None:
            return w
        # try without leading hierarchy (bare)
        bare = base.split(".", 1)[-1]
        w = sigWidths.get(bare)
        if w is not None:
            return w
        # try with forced qualification (in case caller passed bare already)
        forced = f"{instance_name}.{bare}"
        w = sigWidths.get(forced)
        return w

    # ---------- main ----------
    if isinstance(ast, vast.Identifier):
        q = qualify(ast.name)
        w = width_of(q)
        if w is None:
            # default to 1-bit if width unknown (prevents KeyError; upstream can refine later)
            w = 1
        return f"{q}[{w-1}:0]"

    elif isinstance(ast, vast.Partselect):
        base = qualify(ast.var.name)
        msb = to_int(ast.msb)
        lsb = to_int(ast.lsb)
        return f"{base}[{msb}:{lsb}]"

    elif isinstance(ast, vast.Pointer):
        base = qualify(ast.var.name)
        idx = to_int(ast.ptr)
        return f"{base}[{idx}:{idx}]"

    elif isinstance(ast, vast.Unot):
        rname = getSigName(ast.right, instance_name)
        return ['Not', rname]

    elif isinstance(ast, (vast.Or, vast.And, vast.Xor, vast.Eq, vast.NotEq, vast.Sll)):
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
        else:  # vast.Sll
            op = 'Sll'
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        return [op, lname, rname]

    elif isinstance(ast, vast.IntConst):
        return utils.verilogIntConstToInt(ast) if hasattr(utils, "verilogIntConstToInt") else int(str(ast.value), 0)

    elif isinstance(ast, vast.Concat):
        # Return list of child names (MSB..LSB) and let caller expand as needed.
        # NOTE: do not qualify/lift widths here; children handle that.
        return [getSigName(x, instance_name) for x in ast.list[::-1]]

    else:
        # Fallback: treat as identifier-like (defensive)
        q = qualify(str(ast))
        w = width_of(q)
        if w is None:
            w = 1
        return f"{q}[{w-1}:0]"

import re

# --- helpers ---------------------------------------------------------------
_slice_re = re.compile(r'^(?P<base>.+?)\[(?P<msb>\d+):(?P<lsb>\d+)\]$')
_srl_re   = re.compile(r'\(Srl\s+(?P<base>[A-Za-z0-9_\.]+)\s+(?P<sh>\d+)\)\[(?P<msb>\d+):(?P<lsb>\d+)\]')

def _width_of_name(name_or_expr, instance_name: str) -> int:
    # If it's already a constant bit (0/1) treat width as 1
    if name_or_expr in (0, 1):
        return 1

    # List = expression tree (Or/And/Xor/Sll/etc.)
    if isinstance(name_or_expr, list):
        if not name_or_expr:
            return 1
        op = name_or_expr[0]
        # binary bitwise ops — width is max of operand widths
        if op in ('Or', 'And', 'Xor'):
            wl = _width_of_name(name_or_expr[1], instance_name)
            wr = _width_of_name(name_or_expr[2], instance_name)
            return max(wl, wr)
        # logical shift-left: width of the base signal (we keep word size)
        if op == 'Sll':
            return _width_of_name(name_or_expr[1], instance_name)
        # fallback for other list shapes: take max width of children
        w = 0
        for ch in name_or_expr[1:]:
            w = max(w, _width_of_name(ch, instance_name))
        return max(w, 1)

    # Strings: identifier/slice/“(Srl …)” patterns
    if isinstance(name_or_expr, str):
        s = name_or_expr

        m = _slice_re.match(s)
        if m:  # explicit [msb:lsb]
            msb, lsb = int(m.group('msb')), int(m.group('lsb'))
            return abs(msb - lsb) + 1

        m = _srl_re.match(s)  # textual SRL like "(Srl t0_w 24)[0:0]"
        if m:
            msb, lsb = int(m.group('msb')), int(m.group('lsb'))
            return abs(msb - lsb) + 1

        # plain identifier — try exact and instance-qualified names
        return (sigWidths.get(s)
                or sigWidths.get(f"{instance_name}.{s}")
                or 1)

    # Anything else: be conservative
    return 1


def _bit_name(base: str, idx: int) -> str:
    return f"{base}[{idx}:{idx}]"

def _bits_from_slice_string(s: str) -> list:
    m = _slice_re.match(s)
    if not m:
        return [s]
    base, msb, lsb = m.group('base'), int(m.group('msb')), int(m.group('lsb'))
    lo, hi = (lsb, msb) if lsb <= msb else (msb, lsb)
    return [_bit_name(base, i) for i in range(lo, hi+1)]

def _expand_window(expr, low, high, instance_name: str) -> list:
    """Return LSB->MSB list of sources for expr[low..high]."""
    # shift-left operator
    if isinstance(expr, list) and expr and expr[0] == 'Sll':
        base, sh = expr[1], int(expr[2])
        out = []
        for i in range(low, high+1):
            src = i - sh
            if src < 0:
                out.append(0)
            else:
                bbits = _expand_window(base, src, src, instance_name)
                out.append(bbits[0] if bbits else 0)
        return out

    # nested lists — flatten
    if isinstance(expr, list):
        acc = []
        for ch in expr[1:]:
            acc.extend(_expand_window(ch, 0, _width_of_name(getSigName(ch, instance_name), instance_name)-1, instance_name))
        return acc[low:high+1] if acc else [0]*(high-low+1)

    # string cases
    if isinstance(expr, str):
        # normal slice
        if _slice_re.search(expr):
            bits = _bits_from_slice_string(expr)
            need = high - low + 1
            if len(bits) < high+1:
                bits += [0]*((high+1) - len(bits))
            return bits[low:high+1]

        # textual SRL like "(Srl t0_w 24)[0:0]"
        m = _srl_re.search(expr)
        if m:
            base, sh = m.group('base'), int(m.group('sh'))
            out = []
            base_w = _width_of_name(base, instance_name)
            for i in range(low, high+1):
                src = i + sh
                out.append(_bit_name(base, src) if 0 <= src < base_w else 0)
            return out

        # plain identifier
        wid = _width_of_name(expr, instance_name)
        allbits = [_bit_name(expr, i) for i in range(wid)]
        if len(allbits) < high+1:
            allbits += [0]*((high+1) - len(allbits))
        return allbits[low:high+1]

    # fallback: resolve to name via getSigName then recurse
    nm = getSigName(expr, instance_name)
    return _expand_window(nm, low, high, instance_name)

# --- main function ---------------------------------------------------------
def getRnamesExpr(rname, low, high, instance_name: str):
    """
    Handles boolean ops (Or/And/Xor) and bit-slice extraction for any expr tree.
    Returns ['Or', left_bits, right_bits] or a flat list of bits.
    """
    if isinstance(rname, list) and rname and rname[0] in ('Or', 'And', 'Xor'):
        op = rname[0]
        L  = _expand_window(rname[1], low, high, instance_name)
        R  = _expand_window(rname[2], low, high, instance_name)
        w  = high - low + 1
        if len(L) < w: L += [0]*(w - len(L))
        if len(R) < w: R += [0]*(w - len(R))
        return [op, L, R]

    return _expand_window(rname, low, high, instance_name)

def get_formal_port_order(moduleAst):
    """Return ['clk','in','out_1','out_2','rcon'] in declaration order."""
    formals = []
    # ANSI-style module header (common with pyverilog)
    # moduleAst.portlist.ports contains Port / Ioport nodes
    for p in moduleAst.portlist.ports:
        # Ioport -> first child is Input/Output/Inout which contains .name
        # Port    -> .name directly (non-ANSI list, later declarations)
        if hasattr(p, 'first') and p.first is not None:
            decl = p.first  # Input/Output/Inout
            # Some decl nodes have .name; if multiple names, it’s a list in .children()
            if hasattr(decl, 'name') and decl.name is not None:
                formals.append(decl.name)
            else:
                # Handle packed names (rare in headers): gather all
                for ch in decl.children():
                    if hasattr(ch, 'name'):
                        formals.append(ch.name)
        elif hasattr(p, 'name'):
            formals.append(p.name)
    return formals



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

    if inst_list:
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
                # Use formal order from the callee module
                calleeAst = moduleAstMap[inst_module_name]
                formal_names = get_formal_port_order(calleeAst)

                # Assign names by position
                for i, portArg in enumerate(inst.portlist):
                    # Only fill when missing; guard if fewer actuals than formals
                    if portArg.portname in (None, '',):
                        if i < len(formal_names):
                            portArg.portname = formal_names[i]
                        else:
                            # Extra actuals (shouldn't happen normally). Give a stable fallback.
                            portArg.portname = f'__pos{i}__'

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
    moduleWireExprMap[module_name], moduleWireWidthMap[module_name], modTopSortMap = generate_z3.generateModuleMaps(moduleAst, moduleInputPortListMap, moduleOutputPortListMap, moduleInputPortWidthListMap, moduleOutputPortWidthListMap, moduleWireExprMap)

    # populating the widths of the signals in the module/instance
    for w, wid in sorted(moduleWireWidthMap[module_name].items()):
        sigName = '{}.{}'.format(instance_name, w)
        sigWidths[sigName] = wid

    # populating the truth table with expressions corresponding to the wires in the instance
    astProcessed = {}
    for node in modTopSortMap:
        for (key, val) in node.incomingEdgeAstMap.items():
            for ast in val:
                if hash(ast) not in astProcessed:
                    astProcessed[hash(ast)] = True
                    if isinstance(ast, vast.Assign) or isinstance(ast, vast.NonblockingSubstitution):
                        lhsAst = ast.left.var
                        rhsAst = ast.right.var
                        lname = getSigName(lhsAst, instance_name)
                        rname = getSigName(rhsAst, instance_name)

                        lnamesplit = lname.rsplit('[', 1)
                        lnameonly = lnamesplit[0]
                        lbits = lnamesplit[1].split(']')[0]
                        lbits = lbits.split(':')

                        if (lbits[0] == lbits[1]):
                            truthTableMap[lname] = rname

                        else:
                            low = int(lbits[1])
                            high = int(lbits[0])

                            if isinstance(rhsAst, vast.Or) or isinstance(rhsAst, vast.And) or isinstance(rhsAst, vast.Xor) or isinstance(rhsAst, vast.Eq) or isinstance(rhsAst, vast.NotEq) or isinstance(rhsAst, vast.Sll):
                                rnames = getRnamesExpr(rname, low, high, instance_name)
                                for i in range(low, high+1):
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = [rnames[0], rnames[1][i-low], rnames[2][i-low]]

                            elif isinstance(rhsAst, vast.IntConst):
                                bitVals = [int(x) for x in format(rname, str('0'+str(high-low+1)+'b'))]
                                bitVals.reverse()
                                for i in range(low, high+1):
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = bitVals[i-low]

                                # 3) NEW: concatenation on RHS


                            elif isinstance(rhsAst, vast.Concat) or isinstance(rname, list):

                                # Normalize elements coming from getSigName (['Concat', parts]) or raw AST

                                if isinstance(rname, list):

                                    if len(rname) == 2 and rname[0] == 'Concat':

                                        elems = rname[1]  # tagged concat

                                    else:

                                        elems = rname  # already a flat list of parts

                                else:

                                    elems = rhsAst.list  # raw AST concat

                                rhs_bits = []

                                def append_bits_lsb_to_msb(name_or_ast):

                                    nm = getSigName(name_or_ast, instance_name)

                                    # Nested concats: recurse

                                    if isinstance(nm, list):

                                        # Support nested tagged concats consistently

                                        parts = nm[1] if (len(nm) == 2 and nm[0] == 'Concat') else nm

                                        for sub in reversed(parts):
                                            append_bits_lsb_to_msb(sub)

                                        return

                                    # Explicit slice like "foo[31:16]" or "foo[7:7]"

                                    if isinstance(nm, str) and "[" in nm and ":" in nm:

                                        base = nm.split("[", 1)[0]

                                        msb, lsb = nm.split("[", 1)[1].split("]")[0].split(":")

                                        msb, lsb = int(msb), int(lsb)

                                        # LSB -> MSB

                                        for b in range(lsb, msb + 1):
                                            rhs_bits.append(f"{base}[{b}:{b}]")

                                        return

                                    # Single bit pointer like "foo[5]" is usually represented above,

                                    # but if we get a plain name, expand using known width.

                                    base = nm.split("[", 1)[0]

                                    wid = (sigWidths.get(base) or

                                           sigWidths.get(nm) or

                                           sigWidths.get(f"{instance_name}.{base}") or

                                           sigWidths.get(f"{instance_name}.{nm}") or

                                           1)

                                    # LSB -> MSB

                                    for b in range(0, wid):
                                        rhs_bits.append(f"{base}[{b}:{b}]")

                                # Build total RHS bits in overall LSB->MSB order:

                                # iterate concat parts from right (LSB chunk) to left (MSB chunk)

                                for e in reversed(elems):
                                    append_bits_lsb_to_msb(e)

                                need = high - low + 1

                                # Compute width robustly

                                def bitwidth_of(x):
                                    # Tagged concats from getSigName: ['Concat', parts]
                                    if isinstance(x, list):
                                        if len(x) >= 2 and isinstance(x[0], str):
                                            op = x[0]
                                            # Handle tagged concat specifically
                                            if op == 'Concat':
                                                parts = x[1]
                                                return sum(bitwidth_of(p) for p in parts)

                                            # Binary ops we produce in getSigName: ['Or'|'And'|'Xor'|'Eq'|'NotEq'|'Sll', lhs, rhs]
                                            if op in ('Or', 'And', 'Xor'):
                                                return max(bitwidth_of(x[1]), bitwidth_of(x[2]))
                                            if op in ('Eq', 'NotEq'):
                                                return 1
                                            if op == 'Sll':
                                                # keep it simple: width determined by LHS
                                                return bitwidth_of(x[1])

                                            # Unary not: ['Not', expr]
                                            if op == 'Not':
                                                return bitwidth_of(x[1])

                                        # If it's just a flat list (e.g., legacy concat form), sum them
                                        return sum(bitwidth_of(p) for p in x)

                                    # Raw AST concats
                                    if isinstance(x, vast.Concat):
                                        return sum(bitwidth_of(p) for p in x.list)

                                    # Raw AST nodes for ops
                                    if isinstance(x, vast.Or) or isinstance(x, vast.And) or isinstance(x, vast.Xor):
                                        return max(bitwidth_of(x.left), bitwidth_of(x.right))
                                    if isinstance(x, vast.Eq) or isinstance(x, vast.NotEq):
                                        return 1
                                    if isinstance(x, vast.Sll):
                                        return bitwidth_of(x.left)
                                    if isinstance(x, vast.Unot):
                                        return bitwidth_of(x.right)

                                    # Identifiers/slices as strings
                                    if isinstance(x, str):
                                        if '[' in x and ':' in x:
                                            msb, lsb = x.split('[', 1)[1].split(']')[0].split(':')
                                            return abs(int(msb) - int(lsb)) + 1
                                        # Try various namespaced keys
                                        bw = (sigWidths.get(x) or
                                              sigWidths.get(f"{instance_name}.{x}") or 1)
                                        return bw

                                    # Any other AST: resolve to name/string via getSigName, then recurse
                                    try:
                                        nm = getSigName(x, instance_name)
                                        return bitwidth_of(nm)
                                    except Exception:
                                        return 1

                                rhs_width = bitwidth_of(rname if isinstance(rname, list) else rhsAst)

                                assert rhs_width == need, f"Concat width {rhs_width} != LHS width {need} for {lname}"

                                # Map LHS [low..high] to rhs_bits[0..need-1]

                                # rhs_bits is LSB->MSB; i=low maps to rhs_bits[0]

                                for i in range(low, high + 1):
                                    truthTableMap[f'{lnameonly}[{i}:{i}]'] = rhs_bits[i - low]


                            # 4) default: treat RHS as a plain slice (your existing code)

                            else:

                                rnameonly = rname.rsplit('[', 1)[0]

                                rbase = int(rname.split("]")[0].split(":")[1])

                                for i in range(low, high + 1):
                                    truthTableMap[
                                        f'{lnameonly}[{i}:{i}]'] = f'{rnameonly}[{rbase + i - low}:{rbase + i - low}]'
                    elif isinstance(ast, vast.Instance):
                        pass

                    else:
                        assert(False)

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

    return inputNames, inputWidths, signalNames, sigWidths, truthTableMap

import pyverilog.vparser.ast as vast  # already present

def _bm(inst, ident, idx):
    return f"{inst}.{ident}[{idx}:{idx}]"

def _width_of_expr(expr, instance_name, sigWidths):
    if isinstance(expr, vast.Identifier):
        return sigWidths.get(f"{instance_name}.{expr.name}", 1)
    if isinstance(expr, vast.Partselect):
        msb = utils.verilogIntConstToInt(expr.msb)
        lsb = utils.verilogIntConstToInt(expr.lsb)
        return msb - lsb + 1
    return None

def _bm(inst, ident, i): return f"{inst}.{ident}[{i}:{i}]"

def _unwrap_lr(x):
    from pyverilog.vparser.ast import Lvalue, Rvalue
    while isinstance(x, (Lvalue, Rvalue)):
        x = x.var
    return x

def _width_of_expr(expr, inst, sigWidths):
    expr = _unwrap_lr(expr)
    if isinstance(expr, vast.Identifier):
        return sigWidths.get(f"{inst}.{expr.name}", None)
    if isinstance(expr, vast.Partselect):
        msb = utils.verilogIntConstToInt(expr.msb)
        lsb = utils.verilogIntConstToInt(expr.lsb)
        return msb - lsb + 1
    if isinstance(expr, vast.IntConst):
        # width unknown here; caller will fall back to LHS width
        return None
    return None

def _harvest_behavioral_comb_no_unroll(moduleAst, instance_name):
    global truthTableMap, signalNames, sigWidths
    def lhs_w(ident): return sigWidths.get(f"{instance_name}.{ident}", None)

    def ith(expr, i, W_hint=None):
        expr = _unwrap_lr(expr)
        # identifier bit
        if isinstance(expr, vast.Identifier):
            return _bm(instance_name, expr.name, i)
        # slice bit
        if isinstance(expr, vast.Partselect):
            lsb = utils.verilogIntConstToInt(expr.lsb)
            return _bm(instance_name, expr.var.name, lsb + i)
        # constant bit
        if isinstance(expr, vast.IntConst):
            # parse "8'h00" etc. to an integer
            val = utils.verilogIntConstToInt(expr)
            # choose bit i (0 LSB). If W_hint is None, still ok: single-bit fallback
            return (val >> i) & 1
        return None

    def emit_move(lhs_ident, rhs, W):
        for i in range(W):
            y = _bm(instance_name, lhs_ident, i)
            a = ith(rhs, i, W_hint=W)
            if a is None:
                continue
            truthTableMap[y] = a
            signalNames.add(y)
            if isinstance(a, str):
                signalNames.add(a)

    def handle_assign(lhs, rhs):
        lhs = _unwrap_lr(lhs);
        rhs = _unwrap_lr(rhs)
        if not isinstance(lhs, vast.Identifier):
            return
        L = lhs.name

        # Binary ops
        if isinstance(rhs, (vast.And, vast.Or, vast.Xor)):
            A = _unwrap_lr(rhs.left);
            B = _unwrap_lr(rhs.right)
            WA = _width_of_expr(A, instance_name, sigWidths)
            WB = _width_of_expr(B, instance_name, sigWidths)
            W = min(WA, WB) if (WA and WB) else sigWidths.get(f"{instance_name}.{L}", None)
            if not W:
                return
            for i in range(W):
                y = _bm(instance_name, L, i)
                ai = ith(A, i, W_hint=W)
                bi = ith(B, i, W_hint=W)
                if ai is None or bi is None:
                    continue
                op = "And" if isinstance(rhs, vast.And) else ("Or" if isinstance(rhs, vast.Or) else "Xor")
                truthTableMap[y] = [op, ai, bi]
                signalNames.add(y)
                if isinstance(ai, str): signalNames.add(ai)
                if isinstance(bi, str): signalNames.add(bi)
            return

        # Vector id / slice / const → move
        if isinstance(rhs, (vast.Identifier, vast.Partselect, vast.IntConst)):
            W = _width_of_expr(rhs, instance_name, sigWidths) or sigWidths.get(f"{instance_name}.{L}", None)
            if W:
                emit_move(L, rhs, W)
            return

    # continuous assigns
    for item in getattr(moduleAst, 'items', []):
        if isinstance(item, vast.Assign):
            handle_assign(item.left, item.right)

    # always blocks
    for item in getattr(moduleAst, 'items', []):
        if not isinstance(item, vast.Always): continue
        def walk(s):
            if s is None: return
            if isinstance(s, (vast.NonblockingSubstitution, vast.BlockingSubstitution)):
                handle_assign(s.left, s.right); return
            if isinstance(s, vast.Block):
                for t in s.statements: walk(t); return
            if isinstance(s, vast.IfStatement):
                t, f = _if_branches(s)
                walk(t); walk(f); return
            if isinstance(s, vast.CaseStatement):
                for ci in s.caselist:
                    walk(ci.statement)
                return
            if isinstance(s, vast.ForStatement):
                walk(s.statement); return
            # fall-through: try nested 'statement' field
            if hasattr(s, 'statement'): walk(s.statement)
        walk(item.statement)

def _if_branches(node):
    t = getattr(node, 'true_statement', None)
    if t is None:
        t = getattr(node, 'then_stmt', None)
    f = getattr(node, 'false_statement', None)
    if f is None:
        f = getattr(node, 'else_stmt', None)
    return t, f


def _unwrap_lr(x):
    from pyverilog.vparser.ast import Lvalue, Rvalue
    while isinstance(x, (Lvalue, Rvalue)):
        x = x.var
    return x

# This file has been taken from the earlier SOLOMON project, and modified for this project.
# This file contains the functions to parse a Verilog file, and populate various mappings
# with the details of the module under consideration.  (Sequential-aware: supports k-step unrolling)

import time
import copy
import pyverilog.vparser.ast as vast
from pyverilog.vparser.parser import parse

import generate_z3
import utils
import generate_z3_unroll

# map for efficiently calculating truth table entries
truthTableMap = {}
# list of signal names (bit-strings like "inst.net[i:i]")
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
# map from module name to wires list (includes reg + wire)
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

# --------------------------
# Utility
# --------------------------
def _strip_time_suffix(name: str):
    if '@' in name:
        return name.split('@', 1)[0]
    return name

def _safe_width(qname: str, default: int = 1) -> int:
    return sigWidths.get(qname, default)

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
'''    
def populateModuleInputOutputPortListMap(moduleAst):
    module_name = moduleAst.name

    moduleInputPortListMap[module_name] = []
    moduleInputPortWidthListMap[module_name] = []
    moduleOutputPortListMap[module_name] = []
    moduleOutputPortWidthListMap[module_name] = []
    moduleWireListMap[module_name] = []
    moduleWireWidthListMap[module_name] = []

    # --- Case A: Non-ANSI declarations inside the body (Decl) ---
    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Decl):
            for varAst in itemAst.list:
                # width
                if getattr(varAst, "width", None) is not None:
                    w = utils.verilogIntConstToInt(varAst.width.msb) - utils.verilogIntConstToInt(varAst.width.lsb) + 1
                else:
                    w = 1

                if isinstance(varAst, vast.Input):
                    moduleInputPortListMap[module_name].append(varAst.name)
                    moduleInputPortWidthListMap[module_name].append(w)
                elif isinstance(varAst, vast.Output):
                    moduleOutputPortListMap[module_name].append(varAst.name)
                    moduleOutputPortWidthListMap[module_name].append(w)
                elif isinstance(varAst, vast.Inout):
                    # treat inouts as inputs for upstream tracing; also record as outputs if you want
                    moduleInputPortListMap[module_name].append(varAst.name)
                    moduleInputPortWidthListMap[module_name].append(w)
                    moduleOutputPortListMap[module_name].append(varAst.name)
                    moduleOutputPortWidthListMap[module_name].append(w)
                elif isinstance(varAst, (vast.Wire, vast.Reg)):
                    moduleWireListMap[module_name].append(varAst.name)
                    moduleWireWidthListMap[module_name].append(w)

    # --- Case B: ANSI-style ports in the header (Ioport) ---
    # Some designs won’t repeat Input/Output in body Decl; grab them from the header.
    if hasattr(moduleAst, "portlist") and moduleAst.portlist is not None:
        for p in moduleAst.portlist.ports:
            # p can be vast.Port (bare name) or vast.Ioport (with dir decl)
            if isinstance(p, vast.Ioport):
                decl = p.first  # vast.Input / vast.Output / vast.Inout
                name_node = p.second  # vast.Identifier
                port_name = name_node.name if hasattr(name_node, "name") else getattr(decl, "name", None)

                # width
                if getattr(decl, "width", None) is not None:
                    w = utils.verilogIntConstToInt(decl.width.msb) - utils.verilogIntConstToInt(decl.width.lsb) + 1
                else:
                    w = 1

                if isinstance(decl, vast.Input):
                    if port_name not in moduleInputPortListMap[module_name]:
                        moduleInputPortListMap[module_name].append(port_name)
                        moduleInputPortWidthListMap[module_name].append(w)
                elif isinstance(decl, vast.Output):
                    if port_name not in moduleOutputPortListMap[module_name]:
                        moduleOutputPortListMap[module_name].append(port_name)
                        moduleOutputPortWidthListMap[module_name].append(w)
                elif isinstance(decl, vast.Inout):
                    if port_name not in moduleInputPortListMap[module_name]:
                        moduleInputPortListMap[module_name].append(port_name)
                        moduleInputPortWidthListMap[module_name].append(w)
                    if port_name not in moduleOutputPortListMap[module_name]:
                        moduleOutputPortListMap[module_name].append(port_name)
                        moduleOutputPortWidthListMap[module_name].append(w)
            # If it’s vast.Port (no direction info), do nothing here.
'''

def populateModuleInputOutputPortListMap(moduleAst):
    module_name = moduleAst.name

    moduleInputPortListMap[module_name] = []
    moduleInputPortWidthListMap[module_name] = []
    moduleOutputPortListMap[module_name] = []
    moduleOutputPortWidthListMap[module_name] = []
    moduleWireListMap[module_name] = []
    moduleWireWidthListMap[module_name] = []

    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Decl):
            for varAst in itemAst.list:
                if varAst.width is not None:
                    width = utils.verilogIntConstToInt(varAst.width.msb) - utils.verilogIntConstToInt(varAst.width.lsb) + 1
                else:
                    width = 1

                if isinstance(varAst, vast.Input) or isinstance(varAst, vast.Reg):
                    moduleInputPortListMap[module_name].append(varAst.name)
                    moduleInputPortWidthListMap[module_name].append(width)
                elif isinstance(varAst, vast.Output):
                    moduleOutputPortListMap[module_name].append(varAst.name)
                    moduleOutputPortWidthListMap[module_name].append(width)
                elif isinstance(varAst, vast.Wire):
                    moduleWireListMap[module_name].append(varAst.name)
                    moduleWireWidthListMap[module_name].append(width)

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
        rnames = ['{}[{}:{}]'.format(rname, ptr, ptr)]
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

    ins = ['{}.{}'.format(inst_name, p) for p in moduleInputPortListMap[inst_module_name]]
    insWidths = moduleInputPortWidthListMap[inst_module_name][:]
    outs = ['{}.{}'.format(inst_name, p) for p in moduleOutputPortListMap[inst_module_name]]
    outsWidths = moduleOutputPortWidthListMap[inst_module_name][:]

    portnames = [portAst.portname for portAst in inst.portlist]
    if not any(portnames):
        inst.portlist[0].portname = 'o'
        for i in range(1, len(portnames)):
            inst.portlist[i].portname = 'i{}'.format(i)

    all_lnames_inp, all_rnames_inp = [], []
    all_lnames_out, all_rnames_out = [], []

    for x in inst.portlist:
        lname = '{}.{}'.format(inst_name, x.portname)
        if lname in ins:
            wid = insWidths[ins.index(lname)]
            sigWidths[lname] = wid
            lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
            rnames = getRnames(x.argname, instance_name, wid)
            all_lnames_inp.extend(lnames); all_rnames_inp.extend(rnames)

        if lname in outs:
            wid = outsWidths[outs.index(lname)]
            sigWidths[lname] = wid
            lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
            rnames = getRnames(x.argname, instance_name, wid)
            all_lnames_out.extend(lnames); all_rnames_out.extend(rnames)

    key = inst.name if instance_name == '' else inst_name
    instPortInputsMap[key]  = {"lnames": all_lnames_inp[:], "rnames": all_rnames_inp[:]}
    instPortOutputsMap[key] = {"lnames": all_lnames_out[:], "rnames": all_rnames_out[:]}

# extract the sub-circuit of the design influenced by the reference signal bits
def extractSubCircuit(module_name, instance_name, ref_sig_bit_names):
    global truthTableMap, signalNames
    global moduleInputPortListMap, moduleOutputPortListMap
    global moduleInputPortWidthListMap, moduleOutputPortWidthListMap
    global moduleWireExprMap, moduleWireWidthMap
    global instPortInputsMap, instPortOutputsMap

    moduleAst = moduleAstMap[module_name]
    inst_list = getInstListFromAst(moduleAst)

    # populate the input/output port maps for all the instances in the module
    for inst in inst_list:
        populateInstPortInputOutputMap(inst, instance_name)

    # forward trace from reference signals
    forward = set(ref_sig_bit_names[:])
    encounteredSigs = forward.copy()
    reqModInst = set()

    while len(forward) > 0:
        forwardTemp = set()

        for sig in forward:
            for inst in inst_list:
                inst_module_name = inst.module
                inst_name = '{}.{}'.format(instance_name, inst.name)
                key = inst.name if instance_name == '' else inst_name

                if key not in instPortInputsMap:
                    continue

                if sig in instPortInputsMap[key]["rnames"]:
                    if (inst_module_name, inst_name) not in reqModInst:
                        reqModInst.add((inst_module_name, inst_name))

                        for x in instPortInputsMap[key]["rnames"]:
                            signalNames.add(x)

                        getInternalSignalNames(inst_module_name, inst_name)

                        for x in instPortOutputsMap[key]["rnames"]:
                            forwardTemp.add(x)
                            signalNames.add(x)

        forward = set()
        for sig in forwardTemp:
            if sig not in encounteredSigs:
                encounteredSigs.add(sig)
                forward.add(sig)

# constructs the complete signal name(s) from the ast type and instance name
def getSigName(ast, instance_name):
    if isinstance(ast, vast.Identifier):
        qname = '{}.{}'.format(instance_name, ast.name)
        width = _safe_width(qname, 1)
        return '{}[{}:{}]'.format(qname, width-1, 0)
    elif isinstance(ast, vast.Partselect):
        return '{}.{}[{}:{}]'.format(instance_name, ast.var.name, ast.msb, ast.lsb)
    elif isinstance(ast, vast.Pointer):
        qname = '{}.{}'.format(instance_name, ast.var.name)
        ptr = ast.ptr
        return '{}[{}:{}]'.format(qname, ptr, ptr)
    elif isinstance(ast, vast.Unot):
        rname = getSigName(ast.right, instance_name)
        return ['Not', rname]
    elif isinstance(ast, (vast.Or, vast.And, vast.Xor, vast.Eq, vast.NotEq, vast.Sll)):
        if isinstance(ast, vast.Or):   op = 'Or'
        elif isinstance(ast, vast.And): op = 'And'
        elif isinstance(ast, vast.Xor): op = 'Xor'
        elif isinstance(ast, vast.Eq):  op = 'Eq'
        elif isinstance(ast, vast.NotEq): op = 'NotEq'
        elif isinstance(ast, vast.Sll): op = 'Sll'
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        return [op, lname, rname]
    elif isinstance(ast, vast.IntConst):
        return utils.verilogIntConstToInt(ast)
    elif isinstance(ast, vast.Concat):
        return [getSigName(x, instance_name) for x in ast.list[::-1]]

    qname = '{}.{}'.format(instance_name, ast)
    width = _safe_width(qname, 1)
    return '{}[{}:{}]'.format(qname, width-1, 0)

# getting the list of rhs signal names from the signal name and its low, high indices
def getRnamesExpr(rname, low, high):
    if not isinstance(rname, list):
        rbase = int(rname.split("]")[0].split(":")[1])
        return ['{}[{}:{}]'.format(rname.split('[')[0], rbase+i-low, rbase+i-low) for i in range(low, high+1)]

    rnames = [rname[0]]

    if isinstance(rname[1], int):
        bitVals1 = [int(x) for x in format(rname[1], str('0'+str(high-low+1)+'b'))]
        bitVals1.reverse()
        rnames.append(bitVals1)
    else:
        rbase_1 = int(rname[1].split("]")[0].split(":")[1])
        rnames.append(['{}[{}:{}]'.format(rname[1].split('[')[0], rbase_1+i-low, rbase_1+i-low) for i in range(low, high+1)])

    if isinstance(rname[2], int):
        bitVals2 = [int(x) for x in format(rname[2], str('0'+str(high-low+1)+'b'))]
        bitVals2.reverse()
        rnames.append(bitVals2)
    else:
        rbase_2 = int(rname[2].split("]")[0].split(":")[1])
        rnames.append(['{}[{}:{}]'.format(rname[2].split('[')[0], rbase_2+i-low, rbase_2+i-low) for i in range(low, high+1)])

    return rnames

def populateModuleExprMap(module_name, instance_name, k=1):
    """
    Build/refresh module maps for 'module_name' at hierarchical instance 'instance_name',
    unroll sequential logic to depth k, and fill a time-stamped truth table.

    Side-effects (globals updated):
      - moduleInputPortListMap / moduleOutputPortListMap (+ widths)  [ANSI + non-ANSI]
      - moduleWireListMap / moduleWireWidthListMap
      - moduleWireExprMap / moduleWireWidthMap  (expressions at frame k)
      - sigWidths (instance-qualified widths)
      - truthTableMap (time-stamped entries: '<hier.bit>@t' -> ...)
    """
    import pyverilog.vparser.ast as vast

    global moduleInputPortListMap, moduleOutputPortListMap, moduleWireListMap
    global moduleInputPortWidthListMap, moduleOutputPortWidthListMap, moduleWireWidthListMap
    global moduleWireExprMap, moduleWireWidthMap
    global sigWidths, signalNames, truthTableMap  # 'truthTableMap' is your existing dict

    # ---------- small helpers ----------
    def _stamp_t(name: str, t: int) -> str:
        return f"{name}@{t}"

    def _dedup_preserve_last(pairs):
        seen = {}
        for n, w in pairs:
            seen[n] = w
        names = list(seen.keys())
        widths = [seen[n] for n in names]
        return names, widths

    def _width_of(node):
        if getattr(node, 'width', None) is None:
            return 1
        msb = utils.verilogIntConstToInt(node.width.msb)
        lsb = utils.verilogIntConstToInt(node.width.lsb)
        return msb - lsb + 1

    def _collect_ports_with_widths(modAst):
        inputs, outputs = [], []
        # ANSI ports
        if getattr(modAst, 'portlist', None) and modAst.portlist:
            for p in modAst.portlist.ports:
                if isinstance(p, vast.Ioport):
                    decl = p.first   # Input/Output/Inout (may wrap .var)
                    ident = p.second # Identifier (can be None)
                    nm = ident.name if isinstance(ident, vast.Identifier) else (
                         getattr(decl, 'name', None) or
                         (decl.var.name if hasattr(decl, 'var') and isinstance(decl.var, vast.Identifier) else None))
                    if nm is None:
                        continue
                    w = _width_of(decl)
                    if hasattr(decl, 'var') and isinstance(decl.var, vast.Reg) and decl.var.width is not None:
                        w = _width_of(decl.var)
                    if isinstance(decl, vast.Input):  inputs.append((nm, w))
                    if isinstance(decl, vast.Output): outputs.append((nm, w))
        # Non-ANSI decls
        for it in modAst.items:
            if isinstance(it, vast.Decl):
                for v in it.list:
                    if isinstance(v, vast.Input):  inputs.append((v.name,  _width_of(v)))
                    if isinstance(v, vast.Output): outputs.append((v.name, _width_of(v)))
        in_names,  in_ws  = _dedup_preserve_last(inputs)
        out_names, out_ws = _dedup_preserve_last(outputs)
        return (in_names, in_ws), (out_names, out_ws)

    # ---------- fetch module AST ----------
    moduleAst = moduleAstMap[module_name]

    # Ensure port maps (ANSI + non-ANSI) are present for this module
    (moduleInputPortListMap[module_name],  moduleInputPortWidthListMap[module_name]), \
    (moduleOutputPortListMap[module_name], moduleOutputPortWidthListMap[module_name]) = _collect_ports_with_widths(moduleAst)

    # Instance-qualified vectors of ins/outs/wires for THIS module
    ins_m    = [f'{instance_name}.{p}'  for p in moduleInputPortListMap.get(module_name, [])]
    insW_m   =  moduleInputPortWidthListMap.get(module_name, [])[:]
    outs_m   = [f'{instance_name}.{p}'  for p in moduleOutputPortListMap.get(module_name, [])]
    outsW_m  =  moduleOutputPortWidthListMap.get(module_name, [])[:]
    wires_m  = [f'{instance_name}.{w}'  for w in moduleWireListMap.get(module_name, [])]
    wiresW_m =  moduleWireWidthListMap.get(module_name, [])[:]

    # Record widths for this instance's interface + wires
    for s, w in zip(ins_m,  insW_m):  sigWidths[s] = w
    for s, w in zip(outs_m, outsW_m): sigWidths[s] = w
    for s, w in zip(wires_m, wiresW_m): sigWidths[s] = w

    # ---------- handle sub-instances (recursively) ----------
    inst_list = getInstListFromAst(moduleAst)
    if inst_list:
        for inst in inst_list:
            child_mod_name = inst.module
            child_inst_name = f"{instance_name}.{inst.name}"

            # Ensure child module port maps exist
            childAst = moduleAstMap[child_mod_name]
            (moduleInputPortListMap[child_mod_name],  moduleInputPortWidthListMap[child_mod_name]), \
            (moduleOutputPortListMap[child_mod_name], moduleOutputPortWidthListMap[child_mod_name]) = _collect_ports_with_widths(childAst)

            # Build child ins/outs (hier names)
            child_ins   = [f'{child_inst_name}.{p}' for p in moduleInputPortListMap[child_mod_name]]
            child_insW  =  moduleInputPortWidthListMap[child_mod_name][:]
            child_outs  = [f'{child_inst_name}.{p}' for p in moduleOutputPortListMap[child_mod_name]]
            child_outsW =  moduleOutputPortWidthListMap[child_mod_name][:]

            # Ensure portnames on instance connections:
            portnames = [pa.portname for pa in inst.portlist]
            if all(p is None for p in portnames):  # only if *all* unnamed
                # convention: port 0 is 'o', rest 'i1','i2',...
                if len(inst.portlist) >= 1:
                    inst.portlist[0].portname = 'o'
                for i in range(1, len(inst.portlist)):
                    inst.portlist[i].portname = f'i{i}'

            # Connect: parent -> child inputs
            for x in inst.portlist:
                lname = f"{child_inst_name}.{x.portname}"
                if lname in child_ins:
                    wid = child_insW[child_ins.index(lname)]
                    lnames = [f"{lname}[{j}:{j}]" for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)  # parent-side expr names
                    for l, r in zip(lnames, rnames):
                        for tt in range(k + 1):
                            truthTableMap[_stamp_t(l, tt)] = _stamp_t(r, tt)

            # Recurse into child module
            populateModuleExprMap(child_mod_name, child_inst_name, k=k)

            # Connect: child outputs -> parent
            for x in inst.portlist:
                lname = f"{child_inst_name}.{x.portname}"
                if lname in child_outs:
                    wid = child_outsW[child_outs.index(lname)]
                    lnames = [f"{lname}[{j}:{j}]" for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)  # parent wires/nets
                    for l, r in zip(lnames, rnames):
                        for tt in range(k + 1):
                            truthTableMap[_stamp_t(r, tt)] = _stamp_t(l, tt)

    # ---------- SEQUENTIAL: unroll to depth k, capture frame-k symbols ----------
    final_expr_map, final_widths_k, _unused = generate_z3_unroll.generateModuleMapsUnrolled(
        moduleAst,
        moduleInputPortListMap, moduleOutputPortListMap,
        moduleInputPortWidthListMap, moduleOutputPortWidthListMap,
        moduleWireExprMap,  # not used by the unroller but kept for compat
        k
    )

    # ---------- COMBINATIONAL: topo (for truth table structure) ----------
    _, _, modTopSortComb = generate_z3.generateModuleMaps(
        moduleAst,
        moduleInputPortListMap, moduleOutputPortListMap,
        moduleInputPortWidthListMap, moduleOutputPortWidthListMap,
        moduleWireExprMap
    )

    # Ensure dicts for this module
    if module_name not in moduleWireExprMap:
        moduleWireExprMap[module_name] = {}
    if module_name not in moduleWireWidthMap:
        moduleWireWidthMap[module_name] = {}

    # Include declared wires and output ports in expr/width maps (prefer frame-k widths from unroller)
    declared_wires   = set(moduleWireListMap.get(module_name, []))
    declared_outputs = set(moduleOutputPortListMap.get(module_name, []))
    declared_nets    = declared_wires | declared_outputs

    for net in declared_nets:
        tk = f"{net}@{k}"
        # width
        if tk in final_widths_k:
            moduleWireWidthMap[module_name][net] = final_widths_k[tk]
        else:
            try:
                if net in declared_wires:
                    idx = moduleWireListMap[module_name].index(net)
                    moduleWireWidthMap[module_name][net] = moduleWireWidthListMap[module_name][idx]
                else:
                    idx = moduleOutputPortListMap[module_name].index(net)
                    moduleWireWidthMap[module_name][net] = moduleOutputPortWidthListMap[module_name][idx]
            except Exception:
                moduleWireWidthMap[module_name][net] = 1
        # expr at frame k (may be None for pure inputs/regs)
        moduleWireExprMap[module_name][net] = final_expr_map.get(tk, None)

    # instance-qualified widths for this module's wires/outputs
    for w, wid in moduleWireWidthMap[module_name].items():
        sigWidths[f'{instance_name}.{w}'] = wid

    # ---------- Build time-stamped truth table from combinational assigns ----------
    astProcessed = {}
    for node in modTopSortComb:
        for (_, val) in node.incomingEdgeAstMap.items():
            for ast in val:
                h = hash(ast)
                if h in astProcessed:
                    continue
                astProcessed[h] = True

                if isinstance(ast, (vast.Assign, vast.NonblockingSubstitution)):
                    lhsAst = ast.left.var
                    rhsAst = ast.right.var
                    lname  = getSigName(lhsAst, instance_name)  # e.g., 'c17.N10[0:0]'
                    rname  = getSigName(rhsAst, instance_name)

                    lnamesplit = lname.rsplit('[', 1)
                    lnameonly  = lnamesplit[0]
                    lbits      = lnamesplit[1].split(']')[0].split(':')  # ['0','0'] or ['msb','lsb']

                    if lbits[0] == lbits[1]:
                        # single bit
                        for tt in range(k + 1):
                            truthTableMap[_stamp_t(lname, tt)] = _stamp_t(rname, tt)
                    else:
                        # vector slice on LHS — expand bit by bit
                        low = int(lbits[1]); high = int(lbits[0])
                        if isinstance(rhsAst, (vast.Or, vast.And, vast.Xor, vast.Eq, vast.NotEq, vast.Sll)):
                            rnames = getRnamesExpr(rname, low, high)  # returns [op, opers_bits1, opers_bits2] shape in your code
                            for i in range(low, high + 1):
                                for tt in range(k + 1):
                                    truthTable = [_stamp_t(rnames[0], tt)]
                                    # stamp each operand bit path at time tt
                                    truthTable += [_stamp_t(rnames[1][i - low], tt), _stamp_t(rnames[2][i - low], tt)]
                                    truthTableMap[_stamp_t(f'{lnameonly}[{i}:{i}]', tt)] = truthTable
                        elif isinstance(rhsAst, vast.IntConst):
                            bitVals = [int(x) for x in format(rname, str('0' + str(high - low + 1) + 'b'))]
                            bitVals.reverse()
                            for i in range(low, high + 1):
                                for tt in range(k + 1):
                                    truthTableMap[_stamp_t(f'{lnameonly}[{i}:{i}]', tt)] = bitVals[i - low]
                        else:
                            # RHS is another slice/concat/etc. — map bit to bit with base shifting
                            rnameonly = rname.rsplit('[', 1)[0]
                            rbase = int(rname.split("]")[0].split(":")[1])
                            for i in range(low, high + 1):
                                for tt in range(k + 1):
                                    truthTableMap[_stamp_t(f'{lnameonly}[{i}:{i}]', tt)] = _stamp_t(f'{rnameonly}[{rbase + i - low}:{rbase + i - low}]', tt)

                elif isinstance(ast, vast.Instance):
                    # handled via port wiring above
                    pass
                else:
                    # Unexpected AST node in topo; keep strict to catch surprises
                    assert False



# gets the names of all the signals within a module/instance, including internal modules/instances
def getInternalSignalNames(module_name, instance_name):
    global moduleInputPortListMap, moduleOutputPortListMap
    global moduleInputPortWidthListMap, moduleOutputPortWidthListMap
    global moduleWireExprMap, moduleWireWidthMap
    global sigWidths, signalNames

    moduleAst = moduleAstMap[module_name]
    inst_list = getInstListFromAst(moduleAst)

    if inst_list:
        for inst in inst_list:
            inst_module_name = inst.module
            inst_name = '{}.{}'.format(instance_name, inst.name)

            ins = ['{}.{}'.format(inst_name, p) for p in moduleInputPortListMap[inst_module_name]]
            insWidths = moduleInputPortWidthListMap[inst_module_name][:]
            outs = ['{}.{}'.format(inst_name, p) for p in moduleOutputPortListMap[inst_module_name]]
            outsWidths = moduleOutputPortWidthListMap[inst_module_name][:]

            for x in inst.portlist:
                lname = '{}.{}'.format(inst_name, x.portname)
                if lname in ins:
                    wid = insWidths[ins.index(lname)]
                    lnames = ['{}[{}:{}]'.format(lname, j, j) for j in range(wid)]
                    rnames = getRnames(x.argname, instance_name, wid)
                    for s in rnames + lnames:
                        signalNames.add(s)

            # recurse
            getInternalSignalNames(inst_module_name, inst_name)

            # add sub-instance outputs
            for sigName, wid in zip(outs, outsWidths):
                for j in range(wid):
                    signalNames.add(f'{sigName}[{j}:{j}]')

            # add sub-instance wires (from expr map)
            wires = ['{}.{}'.format(inst_name, w1) for w1 in moduleWireExprMap[inst_module_name]]
            print("wires: ", wires)
            wiresWidths = [moduleWireWidthMap[inst_module_name][w1] for w1 in moduleWireExprMap[inst_module_name]]
            for sig, wid in zip(wires, wiresWidths):
                for j in range(wid):
                    signalNames.add(f'{sig}[{j}:{j}]')

    # wires of current module
    wires = ['{}.{}'.format(instance_name, w1) for w1 in moduleWireExprMap[module_name]]
    print("new wires: ", wires)
    wiresWidths = [moduleWireWidthMap[module_name][w1] for w1 in moduleWireExprMap[module_name]]
    for sig, wid in zip(wires, wiresWidths):
        for j in range(wid):
            signalNames.add(f'{sig}[{j}:{j}]')

    # outputs of current module
    outs_m = ['{}.{}'.format(instance_name, p) for p in moduleOutputPortListMap[module_name]]
    outsWidths_m = moduleOutputPortWidthListMap[module_name][:]
    for sig, wid in zip(outs_m, outsWidths_m):
        for j in range(wid):
            signalNames.add(f'{sig}[{j}:{j}]')

# main function to extract the sub-circuit of the design which is influenced by the reference signal bits
def subCircuitExtract(input_file_path, top_module_name, ref_module_name, ref_instance_name, ref_sig_bit_names, k=1):
    """
    Sequential-aware: set k >= 1 to unroll clocked logic k steps.
    Returns (inputNames, inputWidths, signalNames, sigWidths, truthTableMap)
    """
    global truthTableMap, signalNames, sigWidths
    global moduleAstMap, moduleInputPortListMap, moduleOutputPortListMap
    global moduleInputPortWidthListMap, moduleOutputPortWidthListMap
    global moduleWireExprMap, moduleWireWidthMap
    global instPortInputsMap, instPortOutputsMap

    # reset globals for a fresh run
    truthTableMap.clear()
    signalNames.clear()
    sigWidths.clear()
    moduleAstMap.clear()
    moduleInputPortListMap.clear()
    moduleInputPortWidthListMap.clear()
    moduleOutputPortListMap.clear()
    moduleOutputPortWidthListMap.clear()
    moduleWireListMap.clear()
    moduleWireWidthListMap.clear()
    instPortInputsMap.clear()
    instPortOutputsMap.clear()
    moduleWireExprMap.clear()
    moduleWireWidthMap.clear()

    populateModuleAstMap(input_file_path)
    populateModuleInputOutputPortListMap(moduleAstMap[top_module_name])

    print()
    print("Populate module expressions starting...")
    pme_s = time.time()
    populateModuleExprMap(top_module_name, top_module_name, k=k)
    import pprint

    pprint.pprint({
        "moduleInputPortListMap": moduleInputPortListMap,
        "moduleOutputPortListMap": moduleOutputPortListMap,
        "moduleWireListMap": moduleWireListMap,
        "moduleInputPortWidthListMap": moduleInputPortWidthListMap,
        "moduleOutputPortWidthListMap": moduleOutputPortWidthListMap,
        "moduleWireWidthListMap": moduleWireWidthListMap,
        "moduleWireExprMap": moduleWireExprMap,
        "moduleWireWidthMap": moduleWireWidthMap,
        "sigWidths": sigWidths,
        "signalNames": signalNames,
        "truthTableMap": truthTableMap,
    })
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

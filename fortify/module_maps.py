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

    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Decl):
            for varAst in itemAst.list:
                if varAst.width is not None:
                    width = utils.verilogIntConstToInt(varAst.width.msb) - utils.verilogIntConstToInt(varAst.width.lsb) + 1
                else:
                    width = 1

                if isinstance(varAst, vast.Input):
                    moduleInputPortListMap[module_name].append(varAst.name)
                    moduleInputPortWidthListMap[module_name].append(width)
                elif isinstance(varAst, vast.Output):
                    moduleOutputPortListMap[module_name].append(varAst.name)
                    moduleOutputPortWidthListMap[module_name].append(width)
                elif isinstance(varAst, vast.Wire) or isinstance(varAst, vast.Reg):
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
        ptr = utils.verilogIntConstToInt(x.ptr)
        signalName = '{}[{}:{}]'.format(rname, ptr, ptr)
        rnames = [signalName]

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

# constructs the complete signal name(s) from the ast type and instance name
def getSigName(ast, instance_name):
    if isinstance(ast, vast.Identifier):
        sigName = '{}.{}'.format(instance_name, ast.name)
        width = sigWidths[sigName]
        sigName = '{}[{}:{}]'.format(sigName, width-1, 0)
    elif isinstance(ast, vast.Partselect):
        sigName = '{}.{}[{}:{}]'.format(instance_name, ast.var.name, ast.msb, ast.lsb)
    elif isinstance(ast, vast.Pointer):
        sigName = '{}.{}'.format(instance_name, ast.var.name)
        ptr = ast.ptr;
        sigName = '{}[{}:{}]'.format(sigName, ptr, ptr)
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
    elif isinstance(ast, vast.Or) or isinstance(ast, vast.And) or isinstance(ast, vast.Xor) or isinstance(ast, vast.Eq) or isinstance(ast, vast.NotEq) or isinstance(ast, vast.Sll): #or isinstance(ast, vast.Plus):
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
        lname = getSigName(ast.left, instance_name)
        rname = getSigName(ast.right, instance_name)
        sigName = [op, lname, rname]
    elif isinstance(ast, vast.IntConst):
        sigName = utils.verilogIntConstToInt(ast)
    elif isinstance(ast, vast.Concat):
        sigName = [getSigName(x, instance_name) for x in ast.list[::-1]]
    else:
        sigName = '{}.{}'.format(instance_name, ast)
        width = sigWidths.get(sigName, 1)
        sigName = '{}[{}:{}]'.format(sigName, width-1, 0)

    return sigName

# getting the list of rhs signal names from the signal name and its low, high indices
def getRnamesExpr(rname, low, high):
    if not isinstance(rname, list):
        rbase = int(rname.split("]")[0].split(":")[1])
        return ['{}[{}:{}]'.format(rname.split('[')[0], rbase+i-low, rbase+i-low) for i in range(low, high+1)]

    rnames = [rname[0]]
    rbase_1 = 0
    rbase_2 = 0

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
    moduleWireExprMap[module_name], moduleWireWidthMap[module_name], modTopSortMap = generate_z3.generateModuleMaps(moduleAst, moduleInputPortListMap, moduleOutputPortListMap, moduleInputPortWidthListMap, moduleOutputPortWidthListMap, moduleWireExprMap)

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
                        rname = getSigName(rhsAst, instance_name)
                        print("lname: ",lname, " rname : ",rname)
                        if "state" in lname:
                            print("Tali")

                        lnamesplit = lname.rsplit('[', 1)
                        lnameonly = lnamesplit[0]
                        lbits = lnamesplit[1].split(']')[0]
                        lbits = lbits.split(':')

                        low = int(lbits[1])
                        high = int(lbits[0])

                        if isinstance(rhsAst, vast.Plus):
                            # bitwise approximate sum without carry for single-bit too
                            def _bits(expr):
                                try:
                                    return getRnamesExpr(expr, low, high)
                                except Exception:
                                    return None
                            if isinstance(rname, list) and len(rname) >= 3:
                                a_bits = _bits(rname[1])
                                b_bits = _bits(rname[2])
                            else:
                                a_bits = _bits(rname)
                                b_bits = None
                            for i in range(low, high + 1):
                                abit = a_bits[1][i - low] if a_bits and len(a_bits) > 1 else 0
                                bbit = b_bits[1][i - low] if b_bits and len(b_bits) > 1 else 0
                                truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = ['Xor', abit, bbit]

                        elif (lbits[0] == lbits[1]):
                            truthTableMap[lname] = rname

                        else:

                            if isinstance(rhsAst, vast.Or) or isinstance(rhsAst, vast.And) or isinstance(rhsAst, vast.Xor) or isinstance(rhsAst, vast.Eq) or isinstance(rhsAst, vast.NotEq) or isinstance(rhsAst, vast.Sll):
                                rnames = getRnamesExpr(rname, low, high)
                                for i in range(low, high+1):
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = [rnames[0], rnames[1][i-low], rnames[2][i-low]]


                            elif isinstance(rhsAst, vast.IntConst):
                                print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                                # Convert Verilog literal (e.g. "8'h01") to integer
                                const_val = utils.verilogIntConstToInt(rhsAst)
                                width = high - low + 1
                                # Build a binary string of the right width
                                bitstring = format(const_val, '0{}b'.format(width))
                                bitstring = bitstring[::-1]
                                for i in range(low, high + 1):
                                    bit_idx = i - low
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = int(bitstring[bit_idx])
                            elif isinstance(rhsAst, vast.Cond):
                                condName = getSigName(rhsAst.cond, instance_name)
                                tName = getSigName(rhsAst.true_value, instance_name)
                                fName = getSigName(rhsAst.false_value, instance_name)

                                def _bit_at(expr_name, idx):
                                    if isinstance(expr_name, int):
                                        return expr_name
                                    if isinstance(expr_name, list):
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
                                        if len(expr_name) == 3 and expr_name[0] == 'Plus':
                                            left = expr_name[1]
                                            right = expr_name[2]
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
                                            lbit = bit_from_name(left, idx)
                                            rbit = bit_from_name(right, idx)
                                            return ['Xor', lbit, rbit]
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
                                    if isinstance(fbit, list) and len(fbit) == 4 and fbit[0] == 'Cond' and isinstance(fbit[1], list):
                                        inner_cond = fbit[1]
                                        if inner_cond == ['Not', condName] and fbit[3] == 0:
                                            fbit = fbit[2]
                                    bit_exprs[i] = ['Cond', condName, tbit, fbit]

                                def _parse_self_ref(expr):
                                    """Return bit index if expr is a self-reference to lnameonly[idx:idx]."""
                                    if isinstance(expr, str) and expr.startswith(lnameonly + "["):
                                        try:
                                            idx_str = expr.split("[", 1)[1].split(":", 1)[0]
                                            return int(idx_str)
                                        except (IndexError, ValueError):
                                            return None
                                    return None

                                def _build_time_chain(idx, depth):
                                    """Approximate shift over time as an equal mix of successive key bits."""
                                    depth = max(1, depth)
                                    depth = min(depth, high - idx + 1)
                                    key_bits = []
                                    for step in range(depth):
                                        bit_idx = idx + step
                                        key_bits.append(_bit_at(tName, bit_idx))
                                    if not key_bits:
                                        return 0
                                    return ['Mix'] + key_bits

                                depth_limit = min(SHIFT_UNROLL_LIMIT, high - low + 1)
                                for i in range(low, high + 1):
                                    expr = bit_exprs.get(i)
                                    if isinstance(expr, list) and expr[0] == 'Cond':
                                        ref_idx = _parse_self_ref(expr[3])
                                        if ref_idx is not None and ref_idx == i + 1:
                                            chain_expr = _build_time_chain(i, depth_limit)
                                            truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = chain_expr
                                            continue
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = expr
                            elif isinstance(rhsAst, vast.Srl):
                                # logical right shift by constant
                                try:
                                    shift_amt = utils.verilogIntConstToInt(rhsAst.right)
                                except Exception:
                                    shift_amt = 0
                                for i in range(low, high + 1):
                                    src_idx = i + shift_amt
                                    if src_idx <= high:
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = '{}[{}:{}]'.format(lnameonly, src_idx, src_idx)
                                    else:
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = 0
                            elif isinstance(rhsAst, vast.Plus):
                                # bitwise approximate sum without carry: sum_i = a_i XOR b_i
                                def _bits(expr):
                                    try:
                                        return getRnamesExpr(expr, low, high)
                                    except Exception:
                                        return None
                                if isinstance(rname, list) and len(rname) >= 3:
                                    a_bits = _bits(rname[1])
                                    b_bits = _bits(rname[2])
                                else:
                                    a_bits = _bits(rname)
                                    b_bits = None
                                for i in range(low, high + 1):
                                    abit = a_bits[1][i - low] if a_bits and len(a_bits) > 1 else 0
                                    bbit = b_bits[1][i - low] if b_bits and len(b_bits) > 1 else 0
                                    truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = ['Xor', abit, bbit]
                            else:
                                # Check if rname is a list (concatenation or operation result)
                                if isinstance(rname, list):
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
                                                    truthTableMap[dest_bit] = src_bit
                                                    bit_index += 1
                                        elif isinstance(concat_elem, int):
                                            if bit_index <= high:
                                                truthTableMap['{}[{}:{}]'.format(lnameonly, bit_index,
                                                                                 bit_index)] = concat_elem
                                                bit_index += 1
                                else:
                                    # Handle simple signal assignment
                                    try:
                                        rnameonly = rname.rsplit('[', 1)[0]
                                        rbase = int(rname.split("]")[0].split(":")[1])
                                        for i in range(low, high + 1):
                                            truthTableMap['{}[{}:{}]'.format(lnameonly, i, i)] = '{}[{}:{}]'.format(
                                                rnameonly, rbase + i - low, rbase + i - low)
                                    except (IndexError, ValueError):
                                        # Handle malformed signal names gracefully
                                        truthTableMap['{}[{}:{}]'.format(lnameonly, low, high)] = rname
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

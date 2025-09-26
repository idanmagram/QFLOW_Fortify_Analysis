# generate_z3_seq.py
# Sequential-aware Z3 builder with k-step unrolling for Verilog ASTs (PyVerilog)
# Compatible with the earlier "generate_z3.py" style, extended for sequential logic.

import copy
import z3
import pyverilog.vparser.ast as vast

# If you used these in the original project, keep them:
import graph
import utils

# -------------------------
# Basic helpers (unchanged)
# -------------------------

def getInitExpr(width):  # default init to 0
    return z3.BitVecVal(0, width)

def getMaskExpr(total_width, lsb, msb, expr_to_insert):
    msbLength = total_width - msb - 1
    lsbLength = lsb

    if msbLength > 0 and lsbLength > 0:
        return z3.Concat(z3.BitVecVal(0, msbLength), expr_to_insert, z3.BitVecVal(0, lsbLength))
    if msbLength == 0 and lsbLength > 0:
        return z3.Concat(expr_to_insert, z3.BitVecVal(0, lsbLength))
    if msbLength > 0 and lsbLength == 0:
        return z3.Concat(z3.BitVecVal(0, msbLength), expr_to_insert)
    if msbLength == 0 and lsbLength == 0:
        return expr_to_insert
    assert False

def truncateExprToWidth(expr, targetWidth):
    exprWidth = expr.size()
    if exprWidth == targetWidth:
        return expr
    if targetWidth < exprWidth:
        return z3.Extract(targetWidth - 1, 0, expr)
    assert False  # we never auto-extend here

def matchExprWidths(leftExpr, rightExpr):
    lw, rw = leftExpr.size(), rightExpr.size()
    if lw == rw:
        return leftExpr, rightExpr
    if lw < rw:
        return z3.ZeroExt(rw - lw, leftExpr), rightExpr
    return leftExpr, z3.ZeroExt(lw - rw, rightExpr)

def replaceIdentifiers(expr, inputs, args):
    """Substitute Z3 BitVec 'inputs' in 'expr' with 'args' (same widths)."""
    if expr == 0:
        # Keep a sized 0 if someone passed a bogus zero; avoid size() error.
        return expr
    subs = [(i, a) for i, a in zip(inputs, args)]
    return z3.substitute(expr, subs)

def getFunctionDefinitionFromModuleAst(funcName, moduleAst):
    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Function) and itemAst.name == funcName:
            return itemAst
    return None

# -------------------------------------------------
# Expression builder that is time-step aware (t)
# -------------------------------------------------

def getZ3ExprAtTime(
    ast, nameExprTimeMap, nameWidthMap, moduleAst,
    functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap,
    t
):
    """Like your original getZ3Expr..., but:
       - reads identifiers from nameExprTimeMap[(name, t)]
       - keeps all other logic identical
    """
    if isinstance(ast, vast.Identifier):
        name = ast.name
        print("(name, t) ",(name, t))
        assert (name, t) in nameExprTimeMap, f"Missing var {name}@{t}"
        return nameExprTimeMap[(name, t)]

    elif isinstance(ast, vast.Or):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        return z3.simplify(l | r)

    elif isinstance(ast, vast.And):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        return z3.simplify(l & r)

    elif isinstance(ast, vast.Xor):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        return z3.simplify(l ^ r)

    elif isinstance(ast, vast.Eq):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        l, r = matchExprWidths(l, r)
        return z3.simplify(l == r)

    elif isinstance(ast, vast.NotEq):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        l, r = matchExprWidths(l, r)
        return z3.simplify(l != r)

    elif isinstance(ast, vast.Srl):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        l, r = matchExprWidths(l, r)
        return z3.simplify(z3.LShR(l, r))

    elif isinstance(ast, vast.Sll):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        l, r = matchExprWidths(l, r)
        return z3.simplify(z3.LShl(l, r))

    elif isinstance(ast, vast.Pointer):
        varExpr = getZ3ExprAtTime(ast.var, nameExprTimeMap, nameWidthMap, moduleAst,
                                  functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        assert isinstance(ast.ptr, vast.IntConst)
        idx = utils.verilogIntConstToInt(ast.ptr)
        return z3.simplify(z3.Extract(idx, idx, varExpr))

    elif isinstance(ast, vast.Times):
        l = getZ3ExprAtTime(ast.left,  nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        l, r = matchExprWidths(l, r)
        return z3.simplify(l * r)

    elif isinstance(ast, vast.Partselect):
        varExpr = getZ3ExprAtTime(ast.var, nameExprTimeMap, nameWidthMap, moduleAst,
                                  functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        assert isinstance(ast.lsb, vast.IntConst) and isinstance(ast.msb, vast.IntConst)
        lsb = utils.verilogIntConstToInt(ast.lsb)
        msb = utils.verilogIntConstToInt(ast.msb)
        return z3.simplify(z3.Extract(msb, lsb, varExpr))

    elif isinstance(ast, (vast.Lvalue, vast.Rvalue)):
        return getZ3ExprAtTime(ast.var, nameExprTimeMap, nameWidthMap, moduleAst,
                               functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)

    #elif isinstance(ast, vast.Concat):
    #    parts = [getZ3ExprAtTime(x, nameExprTimeMap, nameWidthMap, moduleAst,
    #                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
    #             for x in ast.list]
    #    return z3.simplify(z3.Concat(parts))
    elif isinstance(ast, vast.Concat):
        parts = [getZ3ExprAtTime(x, nameExprTimeMap, nameWidthMap, moduleAst,
                                 functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
                 for x in ast.list]
        return z3.simplify(z3.Concat(*parts))  # <-- note the *

    elif isinstance(ast, vast.Unot):
        r = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        return z3.simplify(~r)

    elif isinstance(ast, vast.Cond):
        c  = getZ3ExprAtTime(ast.cond,        nameExprTimeMap, nameWidthMap, moduleAst,
                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        tv = getZ3ExprAtTime(ast.true_value,  nameExprTimeMap, nameWidthMap, moduleAst,
                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        fv = getZ3ExprAtTime(ast.false_value, nameExprTimeMap, nameWidthMap, moduleAst,
                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        tv, fv = matchExprWidths(tv, fv)
        return z3.simplify(z3.If(c, tv, fv))

    elif isinstance(ast, vast.IntConst):
        width = 32
        if "'" in ast.value:
            idx = ast.value.index("'")
            assert ast.value[:idx].isdigit()
            width = int(ast.value[:idx])
        return z3.BitVecVal(utils.verilogIntConstToInt(ast), width)

    elif isinstance(ast, vast.FunctionCall):
        funcName = ast.name.name
        if funcName not in functionNameExprMap:
            funcAst = getFunctionDefinitionFromModuleAst(funcName, moduleAst)
            getFunctionMaps(funcAst, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
            assert funcName in functionNameExprMap

        functionExpr = functionNameExprMap[funcName]
        newFunctionExpr = copy.deepcopy(functionExpr)

        inputs = functionNameInputListMap[funcName]
        inputs = [z3.BitVec(name, functionNameInputWidthMap[funcName][name]) for name in inputs]
        args = [getZ3ExprAtTime(argAst, nameExprTimeMap, nameWidthMap, moduleAst,
                                functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
                for argAst in ast.args]
        return z3.simplify(replaceIdentifiers(newFunctionExpr, inputs, args))

    elif isinstance(ast, vast.Ulnot):
        x = getZ3ExprAtTime(ast.right, nameExprTimeMap, nameWidthMap, moduleAst,
                            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
        # Logical not: true iff x == 0 (x is a BitVec)
        assert z3.is_bv(x), "Ulnot expects a BitVec expression"
        return z3.simplify(x == z3.BitVecVal(0, x.size()))

    else:
        ast.show()
        print('Warning: Not handling', type(ast), 'file: generate_z3_seq.py', 'line no.:', utils.getLineNumber())
        raise NotImplementedError(type(ast))

# ----------------------------------------
# Function body extraction (same semantics)
# ----------------------------------------

def processBlockingSubstitutionAtTime(statementAst, nameExprTimeMap, nameWidthMap, moduleAst,
                                      functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap,
                                      t):
    lhsAst = statementAst.left.var
    rhsAst = statementAst.right.var
    rhsExpr = getZ3ExprAtTime(rhsAst, nameExprTimeMap, nameWidthMap, moduleAst,
                               functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)

    if isinstance(lhsAst, vast.Identifier):
        width = nameWidthMap[lhsAst.name]
        nameExprTimeMap[(lhsAst.name, t)] = truncateExprToWidth(rhsExpr, width)

    elif isinstance(lhsAst, vast.Pointer):
        assert isinstance(lhsAst.var, vast.Identifier) and isinstance(lhsAst.ptr, vast.IntConst)
        name = lhsAst.var.name
        width = nameWidthMap[name]
        ptr = utils.verilogIntConstToInt(lhsAst.ptr)
        rhsExpr = truncateExprToWidth(rhsExpr, 1)
        maskExpr = getMaskExpr(width, ptr, ptr, rhsExpr)
        nameExprTimeMap[(name, t)] = z3.simplify(nameExprTimeMap[(name, t)] | maskExpr)

    elif isinstance(lhsAst, vast.Partselect):
        assert isinstance(lhsAst.var, vast.Identifier)
        name = lhsAst.var.name
        width = nameWidthMap[name]
        lsb = utils.verilogIntConstToInt(lhsAst.lsb)
        msb = utils.verilogIntConstToInt(lhsAst.msb)
        rhsExpr = truncateExprToWidth(rhsExpr, msb - lsb + 1)
        maskExpr = getMaskExpr(width, lsb, msb, rhsExpr)
        nameExprTimeMap[(name, t)] = z3.simplify(nameExprTimeMap[(name, t)] | maskExpr)

    else:
        print('Warning: Not handling', type(lhsAst), 'in blocking assign')

def getFunctionMaps(funcAst, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    assert isinstance(funcAst, vast.Function)
    assert isinstance(moduleAst, vast.ModuleDef)

    if funcAst.name in functionNameExprMap:
        return

    functionName = funcAst.name
    outputName = functionName + '.' + functionName

    nameExprMap = {}
    nameWidthMap = {}

    outputWidth = utils.verilogIntConstToInt(funcAst.retwidth.msb) - utils.verilogIntConstToInt(funcAst.retwidth.lsb) + 1
    nameExprMap[outputName] = getInitExpr(outputWidth)
    nameWidthMap[outputName] = outputWidth

    inputNameList = []

    # Build function internal network (combinational)
    for ast in funcAst.statement:
        if isinstance(ast, vast.Decl):
            for declAst in ast.list:
                if isinstance(declAst, vast.Reg):
                    width = (utils.verilogIntConstToInt(declAst.width.msb) - utils.verilogIntConstToInt(declAst.width.lsb) + 1) if declAst.width else 1
                    nameExprMap[functionName + '.' + declAst.name] = getInitExpr(width)
                    nameWidthMap[functionName + '.' + declAst.name] = width
                elif isinstance(declAst, vast.Input):
                    width = (utils.verilogIntConstToInt(declAst.width.msb) - utils.verilogIntConstToInt(declAst.width.lsb) + 1) if declAst.width else 1
                    bv = z3.BitVec(functionName + '.' + declAst.name, width)
                    nameExprMap[functionName + '.' + declAst.name] = bv
                    nameWidthMap[functionName + '.' + declAst.name] = width
                    inputNameList.append(functionName + '.' + declAst.name)
        elif isinstance(ast, vast.Block):
            for st in ast.statements:
                if isinstance(st, vast.BlockingSubstitution):
                    processBlockingSubstitution(st, nameExprMap, nameWidthMap, functionName, moduleAst,
                                                functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        elif isinstance(ast, vast.BlockingSubstitution):
            processBlockingSubstitution(ast, nameExprMap, nameWidthMap, functionName, moduleAst,
                                        functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

    functionNameExprMap[functionName] = nameExprMap[outputName]
    functionNameInputWidthMap[functionName] = nameWidthMap
    functionNameInputListMap[functionName] = inputNameList

# Reuse the non-time version for function-body population
def processBlockingSubstitution(statementAst, nameExprMap, nameWidthMap, functionName, moduleAst,
                                functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    lhsAst = statementAst.left.var
    rhsAst = statementAst.right.var
    rhsExpr = getZ3ExprWithFunctionName(rhsAst, nameExprMap, nameWidthMap, functionName, moduleAst,
                                         functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
    if isinstance(lhsAst, vast.Identifier):
        rhsExpr = truncateExprToWidth(rhsExpr, nameWidthMap[functionName + '.' + lhsAst.name])
        nameExprMap[functionName + '.' + lhsAst.name] = rhsExpr
    elif isinstance(lhsAst, vast.Pointer):
        name = functionName + '.' + lhsAst.var.name
        width = nameWidthMap[name]
        ptr = utils.verilogIntConstToInt(lhsAst.ptr)
        rhsExpr = truncateExprToWidth(rhsExpr, 1)
        maskExpr = getMaskExpr(width, ptr, ptr, rhsExpr)
        nameExprMap[name] = z3.simplify(nameExprMap[name] | maskExpr)
    elif isinstance(lhsAst, vast.Partselect):
        name = functionName + '.' + lhsAst.var.name
        width = nameWidthMap[name]
        lsb = utils.verilogIntConstToInt(lhsAst.lsb)
        msb = utils.verilogIntConstToInt(lhsAst.msb)
        rhsExpr = truncateExprToWidth(rhsExpr, msb - lsb + 1)
        maskExpr = getMaskExpr(width, lsb, msb, rhsExpr)
        nameExprMap[name] = z3.simplify(nameExprMap[name] | maskExpr)

def getZ3ExprWithFunctionName(ast, nameExprMap, nameWidthMap, functionName, moduleAst,
                               functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    """Combinational (function context) expr builder — unchanged from your base file."""
    # Small adapter to reuse above time-agnostic logic (Identifier names are prefixed with functionName)
    if isinstance(ast, vast.Identifier):
        nm = functionName + '.' + ast.name
        assert nm in nameExprMap, f"Func id not found: {nm}"
        return nameExprMap[nm]
    # Reuse original expression cases — call into time-less forms by wrapping as if at 'function scope'.
    # For brevity, we just map onto the time-aware builder with a fake single-time map:
    fake_time = 0
    fake_map = {(k.split('.',1)[1], fake_time): v for k, v in nameExprMap.items() if k.startswith(functionName + '.')}
    return getZ3ExprAtTime(ast, fake_map, {k.split('.',1)[1]: w for k, w in nameWidthMap.items()}, moduleAst,
                           functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, fake_time)

# ----------------------------------------
# Sequential extraction & unrolling
# ----------------------------------------

def _is_clocked_always(always_ast, clk_names):
    """Return (is_clocked, edge, clock_name, reset_name_or_None, reset_edge)"""
    if not isinstance(always_ast, vast.Always) or always_ast.sens_list is None:
        return (False, None, None, None, None)
    sens = always_ast.sens_list
    clk = None
    edge = None
    rst = None
    redge = None
    for s in sens.list:
        # s is vast.Sens( vast.Identifier(...), type='posedge'/'negedge'/'all')
        if isinstance(s.sig, vast.Identifier):
            nm = s.sig.name
            typ = s.type
            if nm in clk_names and typ in ('posedge', 'negedge'):
                clk, edge = nm, typ
            # (optional) detect common reset names in sensitivity list
            if nm.lower() in ('rst','reset','rst_n','reset_n'):
                rst, redge = nm, typ
    return (clk is not None, edge, clk, rst, redge)

def _collect_decls_and_ports(moduleAst):
    """Collect widths and classify inputs/outputs/regs/wires from both Decl and ANSI Ioport forms."""
    import pyverilog.vparser.ast as vast

    nameWidthMap = {}
    kinds = {}  # name -> {'input'|'output'|'inout'|'reg'|'wire'|'logic'}

    def _width_from(node):
        if getattr(node, 'width', None) is None:
            return 1
        msb = utils.verilogIntConstToInt(node.width.msb)
        lsb = utils.verilogIntConstToInt(node.width.lsb)
        return msb - lsb + 1

    # 1) ANSI-style ports: module foo(input clk, output reg [7:0] q, ...);
    if getattr(moduleAst, 'portlist', None) is not None and moduleAst.portlist is not None:
        for p in moduleAst.portlist.ports:
            if isinstance(p, vast.Ioport):
                decl = p.first   # e.g., vast.Input / vast.Output / vast.Inout / (possibly nested Reg)
                ident = p.second # usually vast.Identifier with the port name (may be None in some forms)

                # Try to get the port name robustly
                nm = None
                if isinstance(ident, vast.Identifier):
                    nm = ident.name
                elif hasattr(decl, 'name'):
                    nm = decl.name
                elif hasattr(decl, 'var') and isinstance(decl.var, vast.Identifier):
                    nm = decl.var.name

                if nm is None:
                    continue  # skip weird forms

                # Determine width
                w = _width_from(decl)
                # Some tools encode "output reg" as Output with a Reg child; prefer child width/name if present
                if hasattr(decl, 'var') and isinstance(decl.var, vast.Reg) and decl.var.width is not None:
                    w = _width_from(decl.var)

                nameWidthMap[nm] = w

                # Classify kind
                if isinstance(decl, vast.Input):
                    kinds[nm] = 'input'
                elif isinstance(decl, vast.Output):
                    # If it's declared as 'output reg', we still carry it as 'output' here; the 'reg'
                    # storage-ness will also appear in Decl below (if present). That's fine; we create a var either way.
                    kinds[nm] = 'output'
                elif isinstance(decl, vast.Inout):
                    kinds[nm] = 'inout'
                else:
                    # Fallback
                    kinds[nm] = 'logic'

                # If explicitly "output reg" in ANSI form, also mark as reg so S(t) is created
                if hasattr(decl, 'var') and isinstance(decl.var, vast.Reg):
                    kinds[nm] = 'reg'

    # 2) Classic Decls inside the body
    for it in moduleAst.items:
        if isinstance(it, vast.Decl):
            for v in it.list:
                # Figure name
                nm = getattr(v, 'name', None)
                if nm is None and hasattr(v, 'var') and isinstance(v.var, vast.Identifier):
                    nm = v.var.name
                if nm is None:
                    continue

                w = _width_from(v)
                nameWidthMap[nm] = w

                if isinstance(v, vast.Input):
                    kinds[nm] = 'input'
                elif isinstance(v, vast.Output):
                    kinds[nm] = 'output'
                elif isinstance(v, vast.Inout):
                    kinds[nm] = 'inout'
                elif isinstance(v, vast.Reg):
                    kinds[nm] = 'reg'
                elif isinstance(v, vast.Wire):
                    kinds[nm] = 'wire'
                else:
                    kinds[nm] = kinds.get(nm, 'logic')

    return nameWidthMap, kinds


def _inject_implicit_clk_rst_ios(moduleAst, nameWidthMap, kinds, clk_names, rst_names):
    """
    Scan always @(...) sensitivity lists and add undeclared clk/rst as 1-bit inputs.
    Returns a set of names that were injected.
    """
    import pyverilog.vparser.ast as vast
    injected = set()
    known_rst_aliases = set([n.lower() for n in rst_names]) | {'rst', 'reset', 'rst_n', 'reset_n'}
    known_clk_aliases = set([n.lower() for n in clk_names]) | {'clk', 'clock'}
    for it in moduleAst.items:
        if isinstance(it, vast.Always) and it.sens_list is not None:
            for s in it.sens_list.list:
                if isinstance(s.sig, vast.Identifier):
                    nm = s.sig.name
                    low = nm.lower()
                    if (low in known_clk_aliases or low in known_rst_aliases) and nm not in nameWidthMap:
                        nameWidthMap[nm] = 1
                        kinds[nm] = 'input'
                        injected.add(nm)
    return injected

def _declare_time_vars(moduleAst, nameWidthMap, kinds, k, prefix_state='S', prefix_input='I', prefix_wire='W'):
    """Create Z3 BitVecs per time step. Return nameExprTimeMap and per-step catalogs."""
    nameExprTimeMap = {}
    per_t = {'state': [], 'inputs': [], 'wires': [], 'outputs': []}

    def _mk(t, nm, w):
        return z3.BitVec(f"{nm}@{t}", w)

    for t in range(k+1):
        for nm, w in nameWidthMap.items():
            kind = kinds.get(nm, 'wire')
            if kind == 'input':
                bv = _mk(t, nm, w)
                nameExprTimeMap[(nm, t)] = bv
                if t <= k: per_t['inputs'].append((t, nm, bv))
            elif kind == 'output':
                bv = _mk(t, nm, w)
                nameExprTimeMap[(nm, t)] = bv
                per_t['outputs'].append((t, nm, bv))
            elif kind == 'reg':
                bv = _mk(t, nm, w)
                nameExprTimeMap[(nm, t)] = bv
                per_t['state'].append((t, nm, bv))
            else:
                # internal wires/temps also need a symbol per time step
                bv = _mk(t, nm, w)
                nameExprTimeMap[(nm, t)] = bv
                per_t['wires'].append((t, nm, bv))
    return nameExprTimeMap, per_t

def _init_state_constraints(nameExprTimeMap, kinds, nameWidthMap, init_value_cb=None, t0=0):
    """Initial state S(0). Defaults to zeros unless init_value_cb provided."""
    cons = []
    for nm, kind in kinds.items():
        if kind == 'reg':
            w = nameWidthMap[nm]
            init = init_value_cb(nm, w) if init_value_cb else getInitExpr(w)
            cons.append(nameExprTimeMap[(nm, t0)] == init)
    return cons

def _build_comb_assign_constraints(moduleAst, t, nameExprTimeMap, nameWidthMap,
                                   functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    """Handle assign statements and blocking assignments in combinational always @(*) for time t."""
    cons = []
    for it in moduleAst.items:
        if isinstance(it, vast.Assign):
            # continuous assign: lhs = rhs at time t
            lhsAst = it.left.var
            rhsAst = it.right.var
            rhs = getZ3ExprAtTime(rhsAst, nameExprTimeMap, nameWidthMap, moduleAst,
                                   functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
            if isinstance(lhsAst, vast.Identifier):
                nm = lhsAst.name
                w = nameWidthMap[nm]
                cons.append(nameExprTimeMap[(nm, t)] == truncateExprToWidth(rhs, w))
            elif isinstance(lhsAst, vast.Pointer):
                nm = lhsAst.var.name
                idx = utils.verilogIntConstToInt(lhsAst.ptr)
                rhs1 = truncateExprToWidth(rhs, 1)
                w = nameWidthMap[nm]
                maskExpr = getMaskExpr(w, idx, idx, rhs1)
                cons.append(nameExprTimeMap[(nm, t)] == z3.simplify(nameExprTimeMap[(nm, t)] | maskExpr))
            elif isinstance(lhsAst, vast.Partselect):
                nm = lhsAst.var.name
                lsb = utils.verilogIntConstToInt(lhsAst.lsb)
                msb = utils.verilogIntConstToInt(lhsAst.msb)
                rhsN = truncateExprToWidth(rhs, msb - lsb + 1)
                w = nameWidthMap[nm]
                maskExpr = getMaskExpr(w, lsb, msb, rhsN)
                cons.append(nameExprTimeMap[(nm, t)] == z3.simplify(nameExprTimeMap[(nm, t)] | maskExpr))
        elif isinstance(it, vast.Always):
            is_clk, *_ = _is_clocked_always(it, clk_names=[])  # empty: treat none as clocked here
            if not is_clk:
                # combinational always: execute statements as equalities at time t
                stmts = it.statement.statements if isinstance(it.statement, vast.Block) else [it.statement]
                # For blocking (=) inside comb always: just bind equality at time t
                for st in stmts:
                    if isinstance(st, vast.BlockingSubstitution):
                        lhs = st.left.var
                        rhs = st.right.var
                        val = getZ3ExprAtTime(rhs, nameExprTimeMap, nameWidthMap, moduleAst,
                                              functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
                        if isinstance(lhs, vast.Identifier):
                            w = nameWidthMap[lhs.name]
                            cons.append(nameExprTimeMap[(lhs.name, t)] == truncateExprToWidth(val, w))
                        elif isinstance(lhs, vast.Pointer):
                            nm = lhs.var.name; idx = utils.verilogIntConstToInt(lhs.ptr); w = nameWidthMap[nm]
                            val1 = truncateExprToWidth(val, 1)
                            maskExpr = getMaskExpr(w, idx, idx, val1)
                            cons.append(nameExprTimeMap[(nm, t)] == z3.simplify(nameExprTimeMap[(nm, t)] | maskExpr))
                        elif isinstance(lhs, vast.Partselect):
                            nm = lhs.var.name; lsb = utils.verilogIntConstToInt(lhs.lsb); msb = utils.verilogIntConstToInt(lhs.msb)
                            w = nameWidthMap[nm]
                            valN = truncateExprToWidth(val, msb - lsb + 1)
                            maskExpr = getMaskExpr(w, lsb, msb, valN)
                            cons.append(nameExprTimeMap[(nm, t)] == z3.simplify(nameExprTimeMap[(nm, t)] | maskExpr))
    return cons
def _extract_reset_clause(stmt, rst_name, nameExprTimeMap, nameWidthMap, moduleAst,
                          functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t):
    """Detect: if (rst_expr) q <= init; else q <= d;  Return (cond_ast, [(nm,expr)...]_rst, [(nm,expr)...]_nrst)."""
    import pyverilog.vparser.ast as vast
    if not isinstance(stmt, vast.IfStatement):
        return None

    # Does condition reference rst_name (possibly via !rst, partselect, pointer)?
    def _mentions_id(a):
        if isinstance(a, vast.Identifier): return a.name == rst_name
        if isinstance(a, (vast.Unot, vast.Ulnot)): return _mentions_id(a.right)
        if isinstance(a, (vast.Partselect, vast.Pointer)): return _mentions_id(a.var)
        if isinstance(a, vast.Cond):  # (a?b:c) – unlikely in reset, but be safe
            return _mentions_id(a.cond) or _mentions_id(a.true_value) or _mentions_id(a.false_value)
        return False

    if not _mentions_id(stmt.cond):
        return None

    def _collect_nb(subtree):
        if subtree is None:
            return []
        items = subtree.statements if isinstance(subtree, vast.Block) else [subtree]
        return [s for s in items if isinstance(s, vast.NonblockingSubstitution)]

    then_nb = _collect_nb(stmt.true_statement)
    else_nb = _collect_nb(stmt.false_statement)

    def _eqs(nbs):
        eqs = []
        for a in nbs:
            lhs = a.left
            rhs = a.right
            val = getZ3ExprAtTime(rhs, nameExprTimeMap, nameWidthMap, moduleAst,
                                  functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)
            if isinstance(lhs, (vast.Lvalue, vast.Identifier)):
                ident = lhs.var if isinstance(lhs, vast.Lvalue) else lhs
                if isinstance(ident, vast.Identifier):
                    w = nameWidthMap[ident.name]
                    eqs.append((ident.name, truncateExprToWidth(val, w)))
        return eqs

    eq_reset  = _eqs(then_nb)
    eq_nreset = _eqs(else_nb)
    return (stmt.cond, eq_reset, eq_nreset)


def _build_seq_step_constraints(moduleAst, t, tnext, nameExprTimeMap, nameWidthMap,
                                functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap,
                                clk_names, rst_names):
    """
    Build S(t+1) from posedge/negedge clocked always blocks.
    Handles nested Block / If / Case, and NB assigns with Identifier/Partselect/Pointer/Concat LHS.
    """
    import z3
    import pyverilog.vparser.ast as vast

    cons = []

    # ---- helpers ------------------------------------------------------------

    def _z3_at_t(ast_expr):
        return getZ3ExprAtTime(ast_expr, nameExprTimeMap, nameWidthMap, moduleAst,
                               functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap, t)

    def _zeroext_to(width, e):
        ew = e.size() if isinstance(e, z3.Z3NumRef) or z3.is_bv(e) else None
        if ew is None or ew == width:
            return truncateExprToWidth(e, width)
        elif ew < width:
            return z3.ZeroExt(width - ew, e)
        else:
            return truncateExprToWidth(e, width)

    def _mask(w, lsb, msb):
        # width w, inclusive slice [msb:lsb]
        k = (msb - lsb + 1)
        m = ((1 << k) - 1) << lsb
        return z3.BitVecVal(m & ((1 << w) - 1), w)

    def _write_slice(old_val, w, lsb, msb, rhs_bits):
        """Write rhs_bits into [msb:lsb] of old_val (both BV)."""
        rhs_bits = truncateExprToWidth(rhs_bits, msb - lsb + 1)
        cleared = old_val & ~_mask(w, lsb, msb)
        rhs_ext = z3.ZeroExt(w - (msb - lsb + 1), rhs_bits) << lsb
        return z3.simplify(cleared | rhs_ext)

    def _lhs_base_ids(lhs):
        """Return list of base identifier names present in an LHS (used for width/lookups)."""
        if isinstance(lhs, vast.Identifier):
            return [lhs.name]
        if isinstance(lhs, vast.Lvalue):
            return _lhs_base_ids(lhs.var)
        if isinstance(lhs, vast.Pointer):
            return _lhs_base_ids(lhs.var)
        if isinstance(lhs, vast.Partselect):
            return _lhs_base_ids(lhs.var)
        if isinstance(lhs, vast.Concat):
            out = []
            for x in lhs.list:
                out += _lhs_base_ids(x)
            return out
        return []

    def _lhs_write(env_next, lhs, rhs_val):
        """
        Apply a NB write: env_next[nm] := new_expr(env_next.get(nm, old_t), rhs_val) for each nm touched by lhs.
        Supports Identifier, Partselect, Pointer, Concat (by distributing).
        """
        if isinstance(lhs, vast.Lvalue):
            return _lhs_write(env_next, lhs.var, rhs_val)

        if isinstance(lhs, vast.Identifier):
            nm = lhs.name
            w  = nameWidthMap[nm]
            old = env_next.get(nm, nameExprTimeMap[(nm, t)])
            env_next[nm] = truncateExprToWidth(rhs_val, w)
            return

        if isinstance(lhs, vast.Partselect):
            nm = lhs.var.name
            lsb = utils.verilogIntConstToInt(lhs.lsb)
            msb = utils.verilogIntConstToInt(lhs.msb)
            w   = nameWidthMap[nm]
            old = env_next.get(nm, nameExprTimeMap[(nm, t)])
            env_next[nm] = _write_slice(old, w, lsb, msb, rhs_val)
            return

        if isinstance(lhs, vast.Pointer):
            nm = lhs.var.name
            idx = utils.verilogIntConstToInt(lhs.ptr)
            w   = nameWidthMap[nm]
            old = env_next.get(nm, nameExprTimeMap[(nm, t)])
            env_next[nm] = _write_slice(old, w, idx, idx, rhs_val)
            return

        if isinstance(lhs, vast.Concat):
            # Distribute RHS bits across concat elements [MSB .. LSB] per Verilog.
            total = 0
            widths = []
            elems  = list(lhs.list)
            # compute width of each element on the LHS concat
            def _elem_width(e):
                if isinstance(e, vast.Identifier):
                    return nameWidthMap[e.name]
                if isinstance(e, vast.Pointer):
                    return 1
                if isinstance(e, vast.Partselect):
                    l = utils.verilogIntConstToInt(e.lsb)
                    m = utils.verilogIntConstToInt(e.msb)
                    return (m - l + 1)
                # conservative fallback (not ideal for complex forms)
                return 1
            for e in elems:
                ew = _elem_width(e)
                widths.append(ew)
                total += ew
            rhs = truncateExprToWidth(rhs_val, total)

            # Slice RHS and assign to each concat element, from MSB chunk to LSB chunk
            pos_hi = total - 1
            for e, ew in zip(elems, widths):
                pos_lo = pos_hi - ew + 1
                rhs_chunk = z3.Extract(pos_hi, pos_lo, rhs)
                _lhs_write(env_next, e, rhs_chunk)
                pos_hi = pos_lo - 1
            return

        # Unknown LHS: ignore silently (or raise)

    def _collect_updates(stmt):
        """
        Recursively collect NB updates from 'stmt' into a dict: { regname : next_expr_at_t }.
        Within a single clocked always, NB semantics -> last assignment wins.
        For conditionals, build If(cond, then_val, else_val) muxed next-values.
        """
        updates = {}
        if stmt is None:
            return updates

        # Block: execute in order, last-wins
        if isinstance(stmt, vast.Block):
            for s in stmt.statements:
                upd_s = _collect_updates(s)
                # overlay: computed expressions overwrite previous ones
                updates.update(upd_s)
            return updates

        # If: merge per LHS with z3.If; default branch = hold current (or prior update if already present)
        if isinstance(stmt, vast.IfStatement):
            cond = _z3_at_t(stmt.cond)
            upd_t = _collect_updates(stmt.true_statement)
            upd_f = _collect_updates(stmt.false_statement)
            keys = set(upd_t.keys()) | set(upd_f.keys())
            for nm in keys:
                old = updates.get(nm, nameExprTimeMap[(nm, t)])
                val_t = upd_t.get(nm, old)
                val_f = upd_f.get(nm, old)
                updates[nm] = z3.If(cond, val_t, val_f)
            return updates

        # Case: similar merge logic
        if isinstance(stmt, vast.CaseStatement):
            sel = _z3_at_t(stmt.comp)
            # Start from "old"; refine with nested Ite chains in case item order
            # (Synth tools usually treat 'case' as priority-less; here we build a priority chain in source order.)
            # For each case item, collect its updates and guard with (sel == choice) or 'default'.
            def _merge_case(updates_base, caselist):
                out = dict(updates_base)
                default_upd = {}
                guarded = []
                for case in caselist:
                    if isinstance(case, vast.CaseDefault):
                        default_upd = _collect_updates(case.statement)
                        continue
                    # vast.Case(cond, statement)
                    # cond can be a list; we OR them
                    cond_or = None
                    for c in case.cond:
                        cc = _z3_at_t(c)
                        cc = z3.simplify(cc == sel) if z3.is_bv(cc) and z3.is_bv(sel) else (cc == sel)
                        cond_or = cc if cond_or is None else z3.Or(cond_or, cc)
                    guarded.append((cond_or, _collect_updates(case.statement)))

                # Build per-reg ITE chain
                keys = set(out.keys()) | set(default_upd.keys())
                for _, upd in guarded:
                    keys |= set(upd.keys())

                for nm in keys:
                    cur = out.get(nm, nameExprTimeMap[(nm, t)])
                    # default value if nobody hits is current
                    val = default_upd.get(nm, cur)
                    # fold guarded from last to first for source-order priority
                    for cond_i, upd_i in reversed(guarded):
                        vi = upd_i.get(nm, cur)
                        val = z3.If(cond_i, vi, val)
                    out[nm] = val
                return out

            return _merge_case(updates, stmt.caselist)

        # Nonblocking assignment
        if isinstance(stmt, vast.NonblockingSubstitution):
            lhs = stmt.left
            rhs = _z3_at_t(stmt.right)
            _lhs_write(updates, lhs, rhs)
            return updates

        # Some designers use blocking assigns in clocked blocks; model them as well (optional)
        if isinstance(stmt, vast.BlockingSubstitution):
            lhs = stmt.left
            rhs = _z3_at_t(stmt.right)
            _lhs_write(updates, lhs, rhs)
            return updates

        # List/tuple of stmts (defensive)
        if isinstance(stmt, (list, tuple)):
            for s in stmt:
                updates.update(_collect_updates(s))
            return updates

        # Anything else: ignore
        return updates

    # ---- main loop over always blocks --------------------------------------

    for it in moduleAst.items:
        if not isinstance(it, vast.Always):
            continue
        is_clk, edge, clk, rst, redge = _is_clocked_always(it, clk_names)
        if not is_clk:
            continue

        # Collect next-state updates from this always block
        stmt = it.statement if not isinstance(it.statement, vast.Block) else it.statement
        updates = _collect_updates(stmt)

        # Emit constraints nm(t+1) == updates[nm]
        for nm, val in updates.items():
            w = nameWidthMap[nm]
            cons.append(nameExprTimeMap[(nm, tnext)] == truncateExprToWidth(val, w))

    return cons

# ----------------------------------------
# Public API
# ----------------------------------------
def unroll_module(
    moduleAst,
    k,
    clk_names=('clk', 'clock'),
    rst_names=('rst', 'reset', 'rst_n', 'reset_n'),
    init_value_cb=None,
):
    """
    Build a k-step unrolled transition system for 'moduleAst'.
    Returns:
      {
        'vars': { (name,t): z3.BitVec, ... },
        'widths': { name: width },
        'k': k,
        'constraints': [ z3.Expr, ... ],
        'per_t': { 'state':[(t,name,bv)], 'inputs':..., 'wires':..., 'outputs':... },
        'functions': { ... }
      }
    """
    assert isinstance(moduleAst, vast.ModuleDef)
    nameWidthMap, kinds = _collect_decls_and_ports(moduleAst)

    # Inject implicit clk/rst that appear in sensitivity lists but aren't declared.
    _inject_implicit_clk_rst_ios(moduleAst, nameWidthMap, kinds, clk_names, rst_names)

    # Per-time symbols
    nameExprTimeMap, per_t = _declare_time_vars(moduleAst, nameWidthMap, kinds, k)

    # Function caches
    functionNameExprMap = {}
    functionNameInputWidthMap = {}
    functionNameInputListMap = {}
    for it in moduleAst.items:
        if isinstance(it, vast.Function):
            getFunctionMaps(it, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

    constraints = []

    # Initial state S(0)
    constraints += _init_state_constraints(nameExprTimeMap, kinds, nameWidthMap, init_value_cb, t0=0)

    # Combinational constraints at each frame
    for t in range(k + 1):
        constraints += _build_comb_assign_constraints(
            moduleAst, t, nameExprTimeMap, nameWidthMap,
            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap
        )

    # Helper: recursively collect NB/BLK updated LHS base identifiers in a clocked always
    def _collect_nb_lhs_names(node):
        names = set()
        if node is None:
            return names

        def _lhs_ids(lhs):
            if isinstance(lhs, vast.Lvalue):
                return _lhs_ids(lhs.var)
            if isinstance(lhs, vast.Identifier):
                return [lhs.name]
            if isinstance(lhs, (vast.Pointer, vast.Partselect)):
                return _lhs_ids(lhs.var)
            if isinstance(lhs, vast.Concat):
                out = []
                for e in lhs.list:
                    out += _lhs_ids(e)
                return out
            return []

        if isinstance(node, vast.NonblockingSubstitution):
            names |= set(_lhs_ids(node.left))
        elif isinstance(node, vast.BlockingSubstitution):
            names |= set(_lhs_ids(node.left))
        elif isinstance(node, vast.Block):
            for s in node.statements:
                names |= _collect_nb_lhs_names(s)
        elif isinstance(node, vast.IfStatement):
            names |= _collect_nb_lhs_names(node.true_statement)
            names |= _collect_nb_lhs_names(node.false_statement)
        elif isinstance(node, vast.CaseStatement):
            for c in node.caselist:
                names |= _collect_nb_lhs_names(c.statement)
        elif isinstance(node, (list, tuple)):
            for s in node:
                names |= _collect_nb_lhs_names(s)
        return names

    # Sequential transitions: S(t) -> S(t+1)
    for t in range(k):
        tnext = t + 1

        # Explicit next-state updates from clocked always blocks
        constraints += _build_seq_step_constraints(
            moduleAst, t, tnext, nameExprTimeMap, nameWidthMap,
            functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap,
            clk_names, rst_names
        )

        # Implicit holds for regs not written this cycle
        updated_regs = set()
        for it in moduleAst.items:
            if isinstance(it, vast.Always) and _is_clocked_always(it, clk_names)[0]:
                updated_regs |= _collect_nb_lhs_names(it.statement)

        for nm, kind in kinds.items():
            if kind == 'reg' and nm not in updated_regs:
                constraints.append(nameExprTimeMap[(nm, tnext)] == nameExprTimeMap[(nm, t)])
    print("TOP AST name:", moduleAst.name)
    print("All names in nameWidthMap:", sorted(nameWidthMap.keys()))
    print("k =", k)
    return {
        'vars': nameExprTimeMap,
        'widths': nameWidthMap,
        'k': k,
        'constraints': constraints,
        'per_t': per_t,
        'functions': functionNameExprMap,
    }


def generateModuleMapsUnrolled(
    moduleAst,
    moduleInputPortListMap, moduleOutputPortListMap,
    moduleInputPortWidthListMap, moduleOutputPortWidthListMap,
    moduleWireExprMap,
    k,
    clk_names=('clk', 'clock'),
    rst_names=('rst', 'reset', 'rst_n', 'reset_n'),
    init_value_cb=None,
):
    """
    Adapter that matches the old signature expected by module_maps_unroll.populateModuleExprMap.
    Returns (final_expr_map, final_widths_k, modTopSortMap).
    """
    model = unroll_module(moduleAst, k, clk_names=clk_names, rst_names=rst_names, init_value_cb=init_value_cb)

    # Per your earlier logs, callers only need the symbols at the last frame t=k:
    final_expr_map   = { f"{nm}@{k}": bv for (nm, t), bv in model['vars'].items() if t == k }
    final_widths_k   = { f"{nm}@{k}": model['widths'][nm] for (nm, t) in model['vars'].keys() if t == k }

    # The original combinational flow used a topo list (modTopSortMap) to walk assigns.
    # The unroller doesn’t build that; callers should fetch the combinational topo
    # from generate_z3.generateModuleMaps(). To keep the tuple shape, return [] here.
    modTopSortMap = []

    return final_expr_map, final_widths_k, modTopSortMap

# -----------------------------
# Minimal usage demonstration
# -----------------------------
# Assuming you have parsed the Verilog and have a module AST:
#
# from pyverilog.vparser.parser import parse
# ast, _ = parse(['top.v'])
# top = next(m for m in ast.description.definitions if isinstance(m, vast.ModuleDef) and m.name=='top')
# model = unroll_module(top, k=5, clk_names=('clk',), rst_names=('rst_n',), init_value_cb=None)
# s = z3.Solver()
# s.add(model['constraints'])
# # Optionally constrain inputs at each t via model['vars'][('in_sig', t)] == ...
# # Query outputs at final step: model['vars'][('out_sig', 5)]

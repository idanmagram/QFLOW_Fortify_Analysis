# This file has been taken from the earlier SOLOMON project, and modified for this project.
# This file contains the functions necessary to parse a Verilog module AST, given its input
# and output port list and width maps, and obtain the module wire expressions, module width
# maps and topological sort of the AST nodes.

import copy
import z3
import pyverilog.vparser.ast as vast
import graph
import utils

def getInitExpr(width):
    return z3.BitVecVal(0, width)

def getMaskExpr(total_width, lsb, msb, expr_to_insert):
    msbLength = total_width - msb - 1
    lsbLength = lsb

    if msbLength > 0 and lsbLength > 0:
        msbExpr = z3.BitVecVal(0, msbLength)
        lsbExpr = z3.BitVecVal(0, lsbLength)
        return z3.Concat(msbExpr, expr_to_insert, lsbExpr)

    if msbLength == 0 and lsbLength > 0:
        lsbExpr = z3.BitVecVal(0, lsbLength)
        return z3.Concat(expr_to_insert, lsbExpr)

    if msbLength > 0 and lsbLength == 0:
        msbExpr = z3.BitVecVal(0, msbLength)
        return z3.Concat(msbExpr, expr_to_insert)

    if msbLength == 0 and lsbLength == 0:
        return expr_to_insert

    assert(False)

def getFunctionDefinitionFromModuleAst(funcName, moduleAst):
    for itemAst in moduleAst.items:
        if isinstance(itemAst, vast.Function):
            if itemAst.name == funcName:
                return itemAst

def truncateExprToWidth(expr, targetWidth):
    exprWidth = expr.size()
    if exprWidth == targetWidth:
        return expr

    if targetWidth < exprWidth:
        return z3.Extract(targetWidth - 1, 0, expr)

    if targetWidth > exprWidth:
        return z3.ZeroExt(targetWidth - exprWidth, expr)

    assert(False)


def matchExprWidths(leftExpr, rightExpr):
    leftWidth = leftExpr.size()
    rightWidth = rightExpr.size()

    if leftWidth == rightWidth:
        return leftExpr, rightExpr
    if leftWidth < rightWidth:
        return z3.ZeroExt(rightWidth - leftWidth, leftExpr), rightExpr
    if leftWidth > rightWidth:
        return leftExpr, z3.ZeroExt(leftWidth - rightWidth, rightExpr)
    assert(False)

def getZ3ExprWithFunctionName(ast, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    if isinstance(ast, vast.Identifier):
        name = functionName + '.' + ast.name
        assert(name in nameExprMap)
        return nameExprMap[name]

    elif isinstance(ast, vast.Or):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(leftExpr | rightExpr)

    elif isinstance(ast, vast.And):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(leftExpr & rightExpr)

    elif isinstance(ast, vast.Land):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(z3.And(leftExpr, rightExpr))

    elif isinstance(ast, vast.Lor):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(z3.Or(leftExpr, rightExpr))

    elif isinstance(ast, vast.Xor):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(leftExpr ^ rightExpr)

    elif isinstance(ast, vast.Eq):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        leftExpr, rightExpr = matchExprWidths(leftExpr, rightExpr)
        return z3.simplify(leftExpr == rightExpr)


    elif isinstance(ast, vast.Srl):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst,
                                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst,
                                              functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        leftExpr, rightExpr = _coerce_shift_amount(leftExpr, rightExpr)

        return z3.simplify(z3.LShR(leftExpr, rightExpr))

    elif isinstance(ast, vast.Sll):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst,
                                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst,
                                              functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        leftExpr, rightExpr = _coerce_shift_amount(leftExpr, rightExpr)

        return z3.simplify(z3.LShl(leftExpr, rightExpr))

    elif isinstance(ast, vast.Srl):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst,
                                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst,
                                              functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        leftExpr, rightExpr = _coerce_shift_amount(leftExpr, rightExpr)
        return z3.simplify(z3.LShR(leftExpr, rightExpr))

    elif isinstance(ast, vast.Pointer):
        varExpr = getZ3ExprWithFunctionName(ast.var, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        assert(isinstance(ast.ptr, vast.IntConst))
        idx = utils.verilogIntConstToInt(ast.ptr)
        return z3.simplify(z3.Extract(idx, idx, varExpr))

    elif isinstance(ast, vast.Times):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

        leftExpr, rightExpr = matchExprWidths(leftExpr, rightExpr)
        return z3.simplify(leftExpr * rightExpr)

    elif isinstance(ast, vast.Plus):
        leftExpr = getZ3ExprWithFunctionName(ast.left, nameExprMap, nameWidthMap, functionName, moduleAst,
                                             functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst,
                                              functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        leftExpr, rightExpr = matchExprWidths(leftExpr, rightExpr)
        return z3.simplify(leftExpr + rightExpr)

    elif isinstance(ast, vast.Partselect):
        varExpr = getZ3ExprWithFunctionName(ast.var, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        assert(isinstance(ast.lsb, vast.IntConst))
        assert(isinstance(ast.msb, vast.IntConst))
        lsb = utils.verilogIntConstToInt(ast.lsb)
        msb = utils.verilogIntConstToInt(ast.msb)
        return z3.simplify(z3.Extract(msb, lsb, varExpr))

    elif isinstance(ast, vast.Lvalue) or isinstance(ast, vast.Rvalue):
        return getZ3ExprWithFunctionName(ast.var, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

    elif isinstance(ast, vast.Concat):
        return z3.simplify(z3.Concat([getZ3ExprWithFunctionName(item, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap) for item in ast.list]))

    elif isinstance(ast, vast.Cond):
        condExpr = getZ3ExprWithFunctionName(ast.cond, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        trueExpr = getZ3ExprWithFunctionName(ast.true_value, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        falseExpr = getZ3ExprWithFunctionName(ast.false_value, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

        trueExpr, falseExpr = matchExprWidths(trueExpr, falseExpr)
        return z3.simplify(z3.If(condExpr, trueExpr, falseExpr))

    elif isinstance(ast, vast.Unot):
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(~rightExpr)

    elif isinstance(ast, vast.Ulnot):
        rightExpr = getZ3ExprWithFunctionName(ast.right, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        return z3.simplify(~rightExpr)

    elif isinstance(ast, vast.FunctionCall):
        funcName = ast.name.name
        if funcName not in functionNameExprMap:
            funcAst = getFunctionDefinitionFromModuleAst(funcName, moduleAst)
            getFunctionMaps(funcAst, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
            assert(funcName in functionNameExprMap)

        functionExpr = functionNameExprMap[funcName]
        newFunctionExpr = copy.deepcopy(functionExpr)

        inputs = functionNameInputListMap[funcName]
        inputs = [z3.BitVec(name, functionNameInputWidthMap[funcName][name]) for name in inputs]
        args = [getZ3ExprWithFunctionName(argAst, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap) for argAst in ast.args]

        newFunctionExpr = replaceIdentifiers(newFunctionExpr, inputs, args)
        return z3.simplify(newFunctionExpr)

    elif isinstance(ast, vast.Cond):
        condExpr = getZ3ExprWithFunctionName(ast.cond, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        trueExpr = getZ3ExprWithFunctionName(ast.true_value, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
        falseExpr = getZ3ExprWithFunctionName(ast.false_value, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

        trueExpr, falseExpr = matchExprWidths(trueExpr, falseExpr)

        return z3.simplify(z3.If(condExpr, trueExpr, falseExpr))

    elif isinstance(ast, vast.IntConst):
        width = 32
        if "'" in ast.value:
            idx = ast.value.index("'")
            assert(ast.value[:idx].isdigit())
            width = int(ast.value[:idx])

        return z3.BitVecVal(utils.verilogIntConstToInt(ast), width)

    else:
        ast.show()
        print('Warning: Not handling', type(ast), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())

def _coerce_shift_amount(leftExpr, rightExpr):
    """Make rightExpr a BitVec of the same width as leftExpr without changing leftExpr."""
    lw = leftExpr.size()

    # If right is a BitVec, zero-extend or truncate to lw
    if z3.is_bv(rightExpr):
        rw = rightExpr.size()
        if rw < lw:
            rightExpr = z3.ZeroExt(lw - rw, rightExpr)
        elif rw > lw:
            rightExpr = z3.Extract(lw - 1, 0, rightExpr)
        return leftExpr, rightExpr

    # If right is an int numeral (e.g., from IntConst), cast to BitVec of lw
    if z3.is_int_value(rightExpr):
        rightExpr = z3.BitVecVal(rightExpr.as_long(), lw)
        return leftExpr, rightExpr

    # If right is a Python int
    if isinstance(rightExpr, int):
        rightExpr = z3.BitVecVal(rightExpr, lw)
        return leftExpr, rightExpr

    # As a fallback, try to BitVec-ify via zero extension from a 1-bit BV
    # (or raise if that doesn't make sense in your codebase).
    raise TypeError(f"Shift amount must be BitVec or int, got: {rightExpr}")


def _combine_with_guard(cond, guard):
    """Return cond if guard is None else (guard && cond)."""
    if guard is None:
        return cond
    return vast.And(guard, cond)


def _collect_guarded_assigns(stmt, guard, acc):
    """Collect assignments under their guard from an Always statement."""
    if isinstance(stmt, vast.Block):
        for s in stmt.statements:
            _collect_guarded_assigns(s, guard, acc)
    elif isinstance(stmt, vast.NonblockingSubstitution) or isinstance(stmt, vast.BlockingSubstitution):
        target = stmt.left.var
        rhs = stmt.right.var
        acc.setdefault(target, []).append((guard, rhs))
    elif isinstance(stmt, vast.IfStatement):
        cond = stmt.cond

        # --- FIXED BLOCK START ---
        # Changed stmt.then_stmt -> stmt.true_statement
        _collect_guarded_assigns(stmt.true_statement, _combine_with_guard(cond, guard), acc)

        # Changed stmt.else_stmt -> stmt.false_statement
        if stmt.false_statement is not None:
            neg_cond = vast.Unot(cond)
            _collect_guarded_assigns(stmt.false_statement, _combine_with_guard(neg_cond, guard), acc)
        # --- FIXED BLOCK END ---

    elif isinstance(stmt, vast.CaseStatement):
        switch = stmt.comp
        seen_conds = []
        for cas in stmt.caselist:
            cond_list = cas.cond
            # default clause: condition may be None or the string 'default'
            if cond_list is None or cond_list == 'default':
                cond_expr = None
                if seen_conds:
                    ored = seen_conds[0]
                    for other in seen_conds[1:]:
                        ored = vast.Or(ored, other)
                    cond_expr = vast.Unot(ored)
                _collect_guarded_assigns(cas.statement, _combine_with_guard(cond_expr, guard), acc)
            else:
                # cas.cond can be a list/tuple or a single expression
                if not isinstance(cond_list, (list, tuple)):
                    cond_iter = [cond_list]
                else:
                    cond_iter = cond_list
                local_or = None
                for cond_item in cond_iter:
                    eq_expr = vast.Eq(switch, cond_item)
                    local_or = eq_expr if local_or is None else vast.Or(local_or, eq_expr)
                seen_conds.append(local_or)
                _collect_guarded_assigns(cas.statement, _combine_with_guard(local_or, guard), acc)
    else:
        # unhandled statement types are ignored for now
        pass

def _guards_to_mux(assign_list):
    """
    Convert list of (guard, rhs) pairs into a single expression using nested muxes.
    Priority follows list order.
    """
    expr = None
    for guard, rhs in reversed(assign_list):
        if guard is None:
            expr = rhs
        else:
            default_rhs = expr if expr is not None else vast.IntConst("0")
            expr = vast.Cond(guard, rhs, default_rhs)
    return expr if expr is not None else vast.IntConst("0")


def processBlockingSubstitution(statementAst, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    lhsAst = statementAst.left.var
    rhsAst = statementAst.right.var

    rhsExpr = getZ3ExprWithFunctionName(rhsAst, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

    if isinstance(lhsAst, vast.Identifier):
        rhsExpr = truncateExprToWidth(rhsExpr, nameWidthMap[functionName + '.' + lhsAst.name])
        nameExprMap[functionName + '.' + lhsAst.name] = rhsExpr

    elif isinstance(lhsAst, vast.Pointer):
        assert(isinstance(lhsAst.var, vast.Identifier))
        assert(isinstance(lhsAst.ptr, vast.IntConst))

        name = functionName + '.' + lhsAst.var.name
        width = nameWidthMap[name]
        ptr = utils.verilogIntConstToInt(lhsAst.ptr)

        rhsExpr = truncateExprToWidth(rhsExpr, 1)

        maskExpr = getMaskExpr(width, ptr, ptr, rhsExpr)

        nameExprMap[name] = z3.simplify(nameExprMap[name] | maskExpr)

    elif isinstance(lhsAst, vast.Partselect):
        assert(isinstance(lhsAst.var, vast.Identifier))
        assert(isinstance(lhsAst.lsb, vast.IntConst))
        assert(isinstance(lhsAst.msb, vast.IntConst))

        name = functionName + '.' + lhsAst.var.name
        width = nameWidthMap[name]
        lsb = utils.verilogIntConstToInt(lhsAst.lsb)
        msb = utils.verilogIntConstToInt(lhsAst.msb)

        rhsExpr = truncateExprToWidth(rhsExpr, msb - lsb + 1)

        maskExpr = getMaskExpr(width, lsb, msb, rhsExpr)

        nameExprMap[name] = z3.simplify(nameExprMap[name] | maskExpr)

    else:
        print('Warning: Not handling', type(lhsAst), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())

def getFunctionMaps(funcAst, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap):
    assert(isinstance(funcAst, vast.Function))
    assert(isinstance(moduleAst, vast.ModuleDef))

    if funcAst.name in functionNameExprMap or funcAst.name in functionNameInputWidthMap or funcAst.name in functionNameInputListMap:
        print('Repeat calling :', funcAst.name)
        return

    functionName = funcAst.name
    outputName = funcAst.name + '.' + funcAst.name

    nameExprMap = {}
    nameWidthMap = {}

    outputWidth = utils.verilogIntConstToInt(funcAst.retwidth.msb) - utils.verilogIntConstToInt(funcAst.retwidth.lsb) + 1

    nameExprMap[outputName] = getInitExpr(outputWidth)
    nameWidthMap[outputName] = outputWidth

    inputNameList = []

    for ast in funcAst.statement:
        if isinstance(ast, vast.Decl):
            for declAst in ast.list:
                if isinstance(declAst ,vast.Reg):
                    if declAst.width is not None:
                        width = utils.verilogIntConstToInt(declAst.width.msb) - utils.verilogIntConstToInt(declAst.width.lsb) + 1
                    else:
                        width = 1

                    nameExprMap[functionName + '.' + declAst.name] = getInitExpr(width)
                    nameWidthMap[functionName + '.' + declAst.name] = width

                elif isinstance(declAst, vast.Input):
                    if declAst.width is not None:
                        width = utils.verilogIntConstToInt(declAst.width.msb) - utils.verilogIntConstToInt(declAst.width.lsb) + 1
                    else:
                        width = 1

                    nameExprMap[functionName + '.' + declAst.name] = z3.BitVec(functionName + '.' + declAst.name, width)
                    nameWidthMap[functionName + '.' + declAst.name] = width

                    inputNameList.append(functionName + '.' + declAst.name)

                else:
                    print('Warning: Not handling', type(declAst), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())

        elif isinstance(ast, vast.Block):
            for statementAst in ast.statements:
                if isinstance(statementAst, vast.BlockingSubstitution):
                    processBlockingSubstitution(statementAst, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)
                else:
                    print('Warning: Not handling', type(statementAst), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())

        elif isinstance(ast, vast.BlockingSubstitution):
                processBlockingSubstitution(ast, nameExprMap, nameWidthMap, functionName, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

        else:
            print('Warning: Not handling', type(ast), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())

    functionNameExprMap[funcAst.name] = nameExprMap[outputName]
    functionNameInputWidthMap[funcAst.name] = nameWidthMap
    functionNameInputListMap[funcAst.name] = inputNameList

# recursively get all the identifiers involved in an ast
def getIdentifiers(ast):
    if isinstance(ast, vast.Identifier):
        return [ast.name]

    # handles both Concat and LConcat
    elif isinstance(ast, vast.Concat):
        ret = []
        for item in ast.list:
            ret.extend(getIdentifiers(item))
        return ret

    elif isinstance(ast, vast.Or) or isinstance(ast, vast.And) or isinstance(ast, vast.Xor) or isinstance(ast, vast.Eq) or isinstance(ast, vast.NotEq):
        return getIdentifiers(ast.left) + getIdentifiers(ast.right)

    elif isinstance(ast, vast.Unot):
        return getIdentifiers(ast.right)

    elif isinstance(ast, vast.Partselect):
        return getIdentifiers(ast.var) + getIdentifiers(ast.msb) + getIdentifiers(ast.lsb)

    elif isinstance(ast, vast.Pointer):
        return getIdentifiers(ast.var) + getIdentifiers(ast.ptr)

    elif isinstance(ast, vast.Sll):
        return getIdentifiers(ast.left) + getIdentifiers(ast.right)

    elif isinstance(ast, vast.Cond):
        return getIdentifiers(ast.cond) + getIdentifiers(ast.true_value) + getIdentifiers(ast.false_value)

    elif isinstance(ast, vast.FunctionCall):
        ret = []
        for item in ast.args:
            ret.extend(getIdentifiers(item))
        return ret

    elif isinstance(ast, vast.Constant):
        return []

    else:
        print('Warning: Not handling', type(ast), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())
        return []

# add assignment statements to ast
def updateAssignGraph(assignGraph, ast):
    lhsAst = ast.left.var
    rhsAst = ast.right.var

    lhsIdentifiers = list(set(getIdentifiers(lhsAst)))
    rhsIdentifiers = list(set(getIdentifiers(rhsAst)))

    # assert that lhs has atleast one identifier
    assert(lhsIdentifiers)

    if not rhsIdentifiers:
        rhsNode = assignGraph.getNode('~')
        if rhsNode is None:
            rhsNode = graph.GraphNode('~')
            assignGraph.addNode(rhsNode)

        for lhs in lhsIdentifiers:
            lhsNode = assignGraph.getNode(lhs)
            if lhsNode is None:
                lhsNode = graph.GraphNode(lhs)
                assignGraph.addNode(lhsNode)

            rhsNode.addOutgoingEdge(lhsNode.id, ast)
            lhsNode.addIncomingEdge(rhsNode.id, ast)


    for lhs in lhsIdentifiers:
        for rhs in rhsIdentifiers:
            # skip self-edge (e.g., state <= state) to avoid cycles blocking topsort
            if lhs == rhs:
                continue
            lhsNode = assignGraph.getNode(lhs)
            if lhsNode is None:
                lhsNode = graph.GraphNode(lhs)
                assignGraph.addNode(lhsNode)

            rhsNode = assignGraph.getNode(rhs)
            if rhsNode is None:
                rhsNode = graph.GraphNode(rhs)
                assignGraph.addNode(rhsNode)

            rhsNode.addOutgoingEdge(lhsNode.id, ast)
            lhsNode.addIncomingEdge(rhsNode.id, ast)

# replace the variable names in an expression with corresponding arg names
def replaceIdentifiers(expr, inputs, args):
    if expr == 0:
        return z3.BitVecVal(0, expr.size())

    substitutionList = []

    for inp, arg in zip(inputs, args):
        substitutionList.append((inp, arg))

    return z3.substitute(expr, substitutionList)

# add instance related assignments (input/output ports/args) to ast
def updateAssignGraphWithInstAst(assignGraph, instAst, moduleInputPortListMap, moduleOutputPortListMap):
    instInputList = moduleInputPortListMap[instAst.module]
    instOutputList = moduleOutputPortListMap[instAst.module]
    lhsAsts = []
    rhsAsts = []

    for portAst in instAst.portlist:
        if portAst.portname in instInputList:
            rhsAsts.append(portAst.argname)
        elif portAst.portname in instOutputList:
            lhsAsts.append(portAst.argname)
        else:
            # Ignore unexpected port names to be robust to named connections
            continue

    lhsIdentifiers = []
    rhsIdentifiers = []

    for ast in lhsAsts:
        lhsIdentifiers.extend(getIdentifiers(ast))
    for ast in rhsAsts:
        rhsIdentifiers.extend(getIdentifiers(ast))

    lhsIdentifiers = list(set(lhsIdentifiers))
    rhsIdentifiers = list(set(rhsIdentifiers))

    for lhs in lhsIdentifiers:
        for rhs in rhsIdentifiers:
            lhsNode = assignGraph.getNode(lhs)
            if lhsNode is None:
                lhsNode = graph.GraphNode(lhs)
                assignGraph.addNode(lhsNode)

            rhsNode = assignGraph.getNode(rhs)
            if rhsNode is None:
                rhsNode = graph.GraphNode(rhs)
                assignGraph.addNode(rhsNode)

            rhsNode.addOutgoingEdge(lhsNode.id, instAst)
            lhsNode.addIncomingEdge(rhsNode.id, instAst)

# compute the module wire expressions, width maps and topological sort of the nodes in the module ast
def generateModuleMaps(moduleAst, moduleInputPortListMap, moduleOutputPortListMap, moduleInputPortWidthListMap, moduleOutputPortWidthListMap, moduleWireExprMap):
    assert(isinstance(moduleAst, vast.ModuleDef))

    # graph of assign statements
    assignGraph = graph.Graph()
    # map of function name to an expr
    functionNameExprMap = {}
    # map of function name to list of inputs
    functionNameInputListMap = {}
    # map of function name to width of inputs
    functionNameInputWidthMap = {}
    # map of identifier name to expr
    nameExprMap = {}
    # map of identifier name to width
    nameWidthMap = {}
    # map to track assign/instance ASTs which have been processed
    astProcessed = {}

    # process the asts in the module ast to get the declarations and function maps
    for ast in moduleAst.items:
        if isinstance(ast, vast.Decl):
            for varAst in ast.list:
                if varAst.width is not None:
                    width = utils.verilogIntConstToInt(varAst.width.msb) - utils.verilogIntConstToInt(varAst.width.lsb) + 1
                else:
                    width = 1

                if isinstance(varAst, vast.Input) or isinstance(varAst, vast.Reg):
                    nameExprMap[varAst.name] = z3.BitVec(varAst.name, width)
                else:
                    nameExprMap[varAst.name] = getInitExpr(width)

                nameWidthMap[varAst.name] = width

        elif isinstance(ast, vast.Always):
            pass

        elif isinstance(ast, vast.Assign):
            pass

        elif isinstance(ast, vast.InstanceList):
            pass

        elif isinstance(ast, vast.Function):
            getFunctionMaps(ast, moduleAst, functionNameExprMap, functionNameInputWidthMap, functionNameInputListMap)

        else:
            print('Warning: Not handling', type(ast), 'file: generate_z3.py', 'line no.:', utils.getLineNumber())

    # populate graph with assign statements and module instantiations
    for ast in moduleAst.items:
        if isinstance(ast, vast.Assign):
            updateAssignGraph(assignGraph, ast)
        # ADDED: Handle sequential logic in always blocks
        elif isinstance(ast, vast.Always):
            guarded_assigns = {}
            _collect_guarded_assigns(ast.statement, None, guarded_assigns)
            for lhsAst, assign_list in guarded_assigns.items():
                mux_expr = _guards_to_mux(assign_list)
                nb = vast.NonblockingSubstitution(vast.Lvalue(lhsAst), vast.Rvalue(mux_expr))
                updateAssignGraph(assignGraph, nb)

        elif isinstance(ast, vast.InstanceList):
            for instAst in ast.instances:
                updateAssignGraphWithInstAst(assignGraph, instAst, moduleInputPortListMap, moduleOutputPortListMap)

    # do topological sort on graph
    topSortNodes = assignGraph.topSort()

    return nameExprMap, nameWidthMap, topSortNodes

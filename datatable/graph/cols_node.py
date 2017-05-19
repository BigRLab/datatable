#!/usr/bin/env python3
# Copyright 2017 H2O.ai; Apache License Version 2.0;  -*- encoding: utf-8 -*-
import types

from .node import Node
from datatable.expr import DatatableExpr, BaseExpr
from datatable.utils.misc import plural_form as plural
from datatable.utils.misc import normalize_slice



class ColumnSetNode(Node):
    """
    Base class for nodes that create columns of a datatable.

    A ColumnSetNode encapsulates the `select` or `update` arguments of the main
    datatable evaluation function. In the C layer it creates a function that
    constructs and returns a ``Column**`` array of columns.
    """



#===============================================================================

class SliceView_CSNode(ColumnSetNode):

    def __init__(self, dt, start, count, step):
        super().__init__()
        self._dt = dt
        self._start = start
        self._count = count
        self._step = step
        self._cname = ""

    @property
    def dt(self):
        return self._dt

    @property
    def n_columns(self):
        return self._count

    @property
    def n_view_columns(self):
        # All columns are view columns
        return self._count

    @property
    def column_names(self):
        if self._step == 0:
            s = self._dt.names[self._start]
            return tuple([s] * self._count)
        else:
            end = self._start + self._count * self._step
            return self._dt.names[self._start:end:self._step]

    def cget_columns(self):
        """
        Return a name of C function that will create a `Column**` array.

        More specifically, this function will insert into the current evaluation
        context the C code of a function with the following signature:
            Column** get_columns(void);
        This function will allocate and fill the `Column**` array, and then
        relinquish the ownership of that pointer to the caller.
        """
        if not self._cname:
            self._cname = self._gen_c()
        return self._cname


    def _gen_c(self):
        varname = self.context.make_variable_name()
        fnname = "get_" + varname
        ncols = self._count
        dt_isview = self._dt.internal.isview
        if not self.context.has_function(fnname):
            dtvar = self.context.get_dtvar(self._dt)
            fn = "static Column** %s(void) {\n" % fnname
            fn += "    ViewColumn **cols = calloc(%d, sizeof(ViewColumn*));\n" \
                  % (ncols + 1)
            if dt_isview:
                fn += "    Column **srccols = {dt}->source->columns;\n" \
                      .format(dt=dtvar)
            else:
                fn += "    Column **srccols = {dt}->columns;\n".format(dt=dtvar)
            fn += "    if (cols == NULL) return NULL;\n"
            fn += "    int64_t j = %dL;\n" % self._start
            fn += "    for (int64_t i = 0; i < %d; i++) {\n" % ncols
            fn += "        cols[i] = malloc(sizeof(ViewColumn));\n"
            fn += "        if (cols[i] == NULL) return NULL;\n"
            fn += "        cols[i]->srcindex = j;\n"
            fn += "        cols[i]->mtype = MT_VIEW;\n"
            fn += "        cols[i]->stype = srccols[j]->stype;\n"
            fn += "        j += %dL;\n" % self._step
            fn += "    }\n"
            fn += "    cols[%d] = NULL;\n" % ncols
            fn += "    return (Column**) cols;\n"
            fn += "}\n"
            self.context.add_function(fnname, fn)
        return fnname



#===============================================================================

def make_columnset(cols, dt, _nested=False):
    if cols is Ellipsis:
        return SliceView_CSNode(dt, 0, dt.ncols, 1)

    if isinstance(cols, (int, str, slice, BaseExpr)):
        cols = [cols]

    if isinstance(cols, (list, tuple)):
        ncols = dt._ncols
        out = []
        for col in cols:
            if isinstance(col, int):
                if -ncols <= col < ncols:
                    if col < 0:
                        col += ncols
                    out.append((col, dt._names[col]))
                else:
                    n_columns = plural(ncols, "column")
                    raise ValueError(
                        "datatable has %s; column number %d is invalid"
                        % (n_columns, col))
            elif isinstance(col, str):
                if col in dt._inames:
                    out.append((dt._inames[col], col))
                else:
                    raise ValueError(
                        "Column %r not found in the datatable" % col)
            elif isinstance(col, slice):
                start = col.start
                stop = col.stop
                step = col.step
                if isinstance(start, str) or isinstance(stop, str):
                    if start is None:
                        col0 = 0
                    elif isinstance(start, str):
                        if start in dt._inames:
                            col0 = dt._inames[start]
                        else:
                            raise ValueError(
                                "Column name %r not found in the datatable"
                                % start)
                    else:
                        raise ValueError(
                            "The slice should start with a column name: %s"
                            % col)
                    if stop is None:
                        col1 = ncols
                    elif isinstance(stop, str):
                        if stop in dt._inames:
                            col1 = dt._inames[stop] + 1
                        else:
                            raise ValueError("Column name %r not found in "
                                             "the datatable" % stop)
                    else:
                        raise ValueError("The slice should end with a "
                                         "column name: %r" % col)
                    if step is None or step == 1:
                        step = 1
                    else:
                        raise ValueError("Column name slices cannot use "
                                         "strides: %r" % col)
                    if col1 <= col0:
                        col1 -= 2
                        step = -1
                    if len(cols) == 1:
                        count = (col1 - col0) / step
                        return SliceView_CSNode(dt, col0, count, step)
                    else:
                        for i in range(col0, col1, step):
                            out.append((i, dt._names[i]))
                else:
                    if not all(x is None or isinstance(x, int)
                               for x in (start, stop, step)):
                        raise ValueError("%r is not integer-valued" % col)
                    col0, count, step = normalize_slice(col, ncols)
                    if len(cols) == 1:
                        return SliceView_CSNode(dt, col0, count, step)
                    else:
                        for i in range(count):
                            j = col0 + i * step
                            out.append((j, dt._names[j]))

            elif isinstance(col, BaseExpr):
                out.append((col, str(col)))

        return out

    if isinstance(cols, types.FunctionType) and not _nested:
        dtexpr = DatatableExpr(dt)
        res = cols(dtexpr)
        return make_columnset(res, dt, nested=True)

    raise ValueError("Unknown `select` argument: %r" % cols)
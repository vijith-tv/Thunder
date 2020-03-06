#!/usr/bin/env python3

# If not stated otherwise in this file or this component's license file the
# following copyright and licenses apply:
#
# Copyright 2020 RDK Management
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
# C++ header parser
#

import re, uuid, sys, copy, hashlib, os
from collections import OrderedDict
from enum import IntEnum


class ParserError(RuntimeError):
    def __init__(self, msg):
        msg = "%s(%s): parse error: %s" % (CurrentFile(), CurrentLine(), msg)
        super(ParserError, self).__init__(msg)


# Checks if identifier is valid.
def is_valid(token):
    if "operator" in token:
        return re.match(r'^[-\+~<>=!%&^*/\|\[\]]+$', token[8:])
    else:
        validChars = re.match(r'^[a-zA-Z0-9_~]+$', token)
        return token and validChars and not token[0].isdigit()


def ASSERT_ISVALID(token):
    if not is_valid(token):
        raise ParserError("invalid identifier: '" + token + "'")
    elif token in ["alignas", "alignof"]:
        raise ParserError("alignment specifiers are not supported")


def ASSERT_ISEXPECTED(token, list):
    if token not in list:
        raise ParserError("unexpected identifier: '" + token + "', expected one of " + str(list))


# -------------------------------------------------------------------------
# CLASS DEFINITIONS
# -------------------------------------------------------------------------

global_namespace = None


class Ref(IntEnum):
    VALUE = 1
    POINTER = 2
    REFERENCE = 4
    RVALUE_REFERENCE = 8
    CONST = 16,
    VOLATILE = 32,
    CONST_POINTER = 64,
    VOLATILE_POINTER = 128,


class Metadata:
    def __init__(self):
        self.brief = ""
        self.details = ""
        self.input = False
        self.output = False
        self.is_property = False
        self.length = None
        self.maxlength = None
        self.interface = None
        self.param = OrderedDict()
        self.retval = OrderedDict()


class BaseType:
    def __init__(self):
        pass


class Fundamental(BaseType):
    def __init__(self, type):
        BaseType.__init__(self)
        self.type = type

    def Proto(self):
        return self.type

    def __str__(self):
        return self.type

    def __repr__(self):
        return "fundamental %s" % self.type


class Void(Fundamental):
    def __init__(self):
        Fundamental.__init__(self, "void")


class Nullptr_t(Fundamental):
    def __init__(self):
        Fundamental.__init__(self, "nullptr_t")


class Size_t(Fundamental):
    def __init__(self):
        Fundamental.__init__(self, "size_t")


class Time_t(Fundamental):
    def __init__(self):
        Fundamental.__init__(self, "time_t")


class Bool(Fundamental):
    def __init__(self):
        Fundamental.__init__(self, "bool")


class Integer(Fundamental):
    def __init__(self, string):
        Fundamental.__init__(self, string)
        self.signed = "unsigned" not in self.type and "uint" not in self.type
        if self.type == "char":
            self.size = "char"
        elif self.type == "wchar_t":
            self.size = "wchar"
        elif "8" in self.type:
            self.size = "char"
        elif "16" in self.type:
            self.size = "short"
        elif "32" in self.type:
            self.size = "long"
        elif "64" in self.type:
            self.size = "long long"
        else:
            self.size = " ".join(self.type.split()[1:])


class String(Fundamental):
    def __init__(self, std=False):
        Fundamental.__init__(self, "std::string" if std else "string")


class Float(Fundamental):
    def __init__(self, string):
        Fundamental.__init__(self, string)


# Holds identifier type
class Identifier():
    def __init__(self, parent_block, parent, string, valid_specifiers, tags_allowed=True):
        self.parent = parent_block
        self.meta = Metadata()
        parent.specifiers = []
        self.name = ""
        type = ["?"] # indexing safety
        type_found = False
        nest1 = 0
        nest2 = 0
        array = False
        skip = 0

        if string.count("*") > 1:
            raise ParserError("pointers to pointers are not supported: '%s'" % (" ".join(string)))
        elif string.count("[") > 1:
            raise ParserError("multi-dimensional arrays are not supported: '%s'" % (" ".join(string)))
        elif "[" in string and "*" in string:
            raise ParserError("arrays of pointers are not supported: '%s'" % (" ".join(string)))
        elif "&&" in string:
            raise ParserError("rvalue references are not supported: '%s'" % (" ".join(string)))

        for i, token in enumerate(string):
            if not token:
                continue

            if isinstance(token, Type):
                # encountered a token that has already been parsed
                type.append(token)
                type_found = True
                continue

            if skip > 0:
                skip -= 1
                continue

            # just keep together anything that comes within <> or () brackets
            # template arguments will be parsed when/if instantiated
            if token == "(":
                type[-1] += " ("
                type_found = False
                nest1 += 1
            elif token == ")":
                type[-1] += " )"
                if nest1 == 0 and not nest2:
                    type_found = True
                nest2 -= 1
            elif token == "<":
                type.append("<")
                type_found = False
                nest2 += 1
            elif token == ">":
                type[-1] += " >"
                nest2 -= 1
                if nest2 == 0 and not nest1:
                    type_found = True
            elif nest1 or nest2:
                type[-1] += " " + token

            # handle pointer/reference markers
            elif token[0] == "@":
                if token[1:] == "IN":
                    if tags_allowed:
                        self.meta.input = True
                    else:
                        raise ParserError("in/out tags not allowed on return value")
                elif token[1:] == "OUT":
                    if tags_allowed:
                        self.meta.output = True
                    else:
                        raise ParserError("in/out tags not allowed on return value")
                elif token[1:] == "LENGTH":
                    self.meta.length = string[i + 1]
                    skip = 1
                    continue
                elif token[1:] == "MAXLENGTH":
                    if tags_allowed:
                        self.meta.maxlength = string[i + 1]
                    else:
                        raise ParserError("maxlength tag not allowed on return value")
                    skip = 1
                    continue
                elif token[1:] == "INTERFACE":
                    self.meta.interface = string[i + 1]
                    skip = 1
                elif token[1:] == "PROPERTY":
                    self.meta.is_property = True
                elif token[1:] == "BRIEF":
                    self.meta.brief = string[i + 1]
                    skip = 1
                elif token[1:] == "DETAILS":
                    self.meta.details = string[i + 1]
                    skip = 1
                elif token[1:] == "PARAM":
                    self.meta.param[string[i + 1]] = string[i + 2]
                    skip = 2
                elif token[1:] == "RETVAL":
                    self.meta.retval[string[i + 1]] = string[i + 2]
                    skip = 2
                else:
                    raise ParserError("invalid tag: " + token)

            # skip C-style explicit struct
            elif token in ["struct", "class", "union"]:
                continue
            elif token in ["export"]: # skip
                continue

            # keep identifers with scope operator together
            elif token == "::":
                if len(type) > 1:
                    type[-1] += "::"
                type_found = False

            # arrays are equivalent to pointers here, so make it uniform
            # disregard anything that's inside the brackets
            elif token == "[":
                array = True
            elif token == "]":
                array = False
                type.append("*")

            elif token in ["*", "&"]:
                type.append(token)

            elif token in ["const", "volatile", "constexpr"]:
                if token == "constexpr":
                    parent.specifiers.append("constexpr")
                    token = "const"

                # put qualifiers in order
                if "*" in type:
                    type.insert(type.index("*") + 1, token)
                elif "&" in type:
                    type.insert(type.index("&") + 1, token)
                else:
                    type.insert(1, token)

            # include valid specifiers
            elif token in valid_specifiers:
                parent.specifiers.append(token)

            elif not type_found and not array:
                # handle primitive type combinations...
                if isinstance(type[-1], str):
                    if (token in ["int"]) and (type[-1].split()[-1] in ["signed", "unsigned", "short", "long"]):
                        type[-1] += " " + token
                    elif (token in ["char", "short", "long"]) and (type[-1].split()[-1] in ["signed", "unsigned"]):
                        type[-1] += " " + token
                    elif (token in ["long", "double"]) and (type[-1].split()[-1] in ["long"]):
                        type[-1] += " " + token
                    # keep identifers with scope operator together
                    elif type[-1].endswith("::"):
                        type[-1] += token
                    # keep together anything that comes within <> or () brackets
                    elif nest1 == 0 and nest2 == 0:
                        type.append(token)
                    else:
                        type[-1] += token

                if ((i == len(string) - 1) or (string[i + 1] not in ["char", "short", "long", "int", "double"])):
                    type_found = True

            elif type_found:
                if not array:
                    self.name = token

        if array:
            raise ParserError("unmatched bracket '['")

        type = type[1:]
        self.type = type

        # Normalize fundamental types
        if type and isinstance(type[-1], str):
            t = type[-1]
            if t.split()[-1] in ["char", "short", "long", "int", "double", "float", "signed", "unsigned"]:
                if "double" in t:
                    type[-1] = "long double" if "long" in t else "double"
                elif "float" in t:
                    type[-1] = "float"
                elif "long" in t:
                    if t.count("long") == 1:
                        type[-1] = "unsigned long" if "unsigned" in t else "signed long"
                    else:
                        type[-1] = "unsigned long long" if "unsigned" in t else "signed long long"
                elif "short" in t:
                    type[-1] = "unsigned short" if "unsigned" in t else "signed short"
                elif "char" in t:
                    type[-1] = "unsigned char" if "unsigned" in t else "signed char" if "signed" in t else "char"
                elif "int" in t or "signed" in t or "unsigned" in t:
                    type[-1] = "unsigned int" if "unsigned" in t else "signed int"

        # Try to match the type to an already defined class...
        self.ResolveIdentifiers(parent_block)

    def ResolveIdentifiers(self, parent):
        if isinstance(parent, Method):
            parent = parent.parent

        if self.type:

            def __Search(tree, found, T):
                qualifiedT = "::" + T

                # need full qualification if the class is a subclass
                if tree.full_name.startswith(parent.full_name + "::"):
                    if T.count("::") != tree.full_name.replace(parent.full_name, "").count("::"):
                        return

                enum_match = [e for e in tree.enums if e.full_name.endswith(qualifiedT)]
                typedef_match = [td for td in tree.typedefs if td.full_name.endswith(qualifiedT)]
                class_match = [cl for cl in tree.classes if cl.full_name.endswith(qualifiedT)]

                template_match = []
                if isinstance(tree, TemplateClass):
                    template_match = [t for t in tree.parameters if t.full_name.endswith(qualifiedT)]

                found += enum_match + typedef_match + class_match + template_match

                if isinstance(tree, (Namespace, Class)):
                    for c in tree.classes:
                        __Search(c, found, T)

                if isinstance(tree, Namespace):
                    for n in tree.namespaces:
                        __Search(n, found, T)

            # find the type to scan for...
            typeIdx = len(self.type) - 1
            cnt = 0
            ref = 0
            while self.type[typeIdx] in ["*", "&", "&&", "const", "volatile"]:
                if self.type[typeIdx] == "*":
                    ref |= Ref.POINTER
                elif self.type[typeIdx] == "&":
                    ref |= Ref.REFERENCE
                elif self.type[typeIdx] == "&&":
                    ref |= Ref.RVALUE_REFERENCE
                elif self.type[typeIdx] == "const":
                    ref |= Ref.CONST_POINTER
                elif self.type[typeIdx] == "volatile":
                    ref |= Ref.VOLATILE_POINTER
                typeIdx -= 1

            # Skip template parsing here
            if isinstance(self.type[typeIdx], str):
                if self.type[typeIdx][0] == "<":
                    typeIdx -= 1

            if isinstance(self.type[typeIdx], str):
                i = typeIdx
                type = self.type[i].split()[-1]
                if type in ["float", "double"]:
                    self.type[i] = Type(Float(self.type[i]))
                elif type in [
                        "int", "char", "wchar_t", "short", "long", "signed", "unsigned", "int8_t", "uint8_t", "int16_t",
                        "uint16_t", "int32_t", "uint32_t", "int64_t", "uint64_t"
                ]:
                    self.type[i] = Type(Integer(self.type[i]))
                elif type == "bool":
                    self.type[i] = Type(Bool())
                elif type == "void":
                    self.type[i] = Type(Void())
                elif type in ["size_t", "std::size_t"]:
                    self.type[i] = Type(Size_t())
                elif type in ["time_t", "std::time_t"]:
                    self.type[i] = Type(Time_t())
                elif type in ["nullptr_t", "std::nullptr_t"]:
                    self.type[i] = Type(Nullptr_t())
                elif type == "string":
                    self.type[i] = Type(String())
                elif type == "std::string":
                    self.type[i] = Type(String(True))
                else:
                    found = []
                    __Search(global_namespace, found, self.type[i])
                    if found:
                        # take closest match
                        found = found[-1]
                        if isinstance(found, TemplateClass):
                            # if we're pointing to a class template, then let's instantiate it!
                            self.type[i] = Type(found.Instantiate(self.type[i + 1]))
                            del self.type[i + 1]
                        else:
                            self.type[i] = found if isinstance(found, TemplateTypeParameter) else Type(found)

                if isinstance(self.type[typeIdx], Type):
                    self.type[typeIdx].ref = ref

            if isinstance(self.type[typeIdx], Type):
                for i in range(len(self.type) - cnt - 1):
                    if self.type[i] == "const":
                        self.type[typeIdx].ref |= Ref.CONST
                    elif self.type[i] == "volatile":
                        self.type[typeIdx].ref |= Ref.VOLATILE

                self.type = self.type[typeIdx]

    def __str__(self):
        return str(self.type) if self.type else ""

    def __repr__(self):
        return str(self)

    def Type(self):
        return self.type

    def Proto(self):
        return str(self.Type())


def Evaluate(identifiers_):
    # Ensure scoped identifiers are kpt together
    identifiers = ["?"]
    for i, id in enumerate(identifiers_):
        if id == "::" or identifiers[-1].endswith("::"):
            identifiers[-1] += id
        else:
            identifiers.append(id)
    del identifiers[0]

    val = []
    if identifiers:
        for identifier in identifiers:
            try:
                identifier = identifier
            except:
                pass
            try:
                val.append(str(int(identifier, 16 if identifier[:2] == "0x" else 10)))
            except:

                def __Search(tree, found, T):
                    var_match = [v for v in tree.vars if v.full_name.endswith(T)]

                    enumerator_match = []
                    for e in tree.enums:
                        enumerator_match += [item for item in e.items if item.full_name.endswith(T)]

                    template_match = []
                    if (isinstance(tree, TemplateClass)):
                        template_match = [t for t in tree.arguments if t.full_name.endswith(T)]

                    found += var_match + enumerator_match + template_match

                    if isinstance(tree, (Namespace, Class)):
                        for c in tree.classes:
                            __Search(c, found, T)

                    if isinstance(tree, Namespace):
                        for n in tree.namespaces:
                            __Search(n, found, T)

                found = []
                __Search(global_namespace, found, "::" + identifier)
                if found:
                    val.append(found[-1])
                else:
                    val.append(str(identifier))

    if not val:
        val = identifiers

    value = None

    # attempt to parse the arithmetics...
    try:
        x = [str(v.value) if (isinstance(v, (Variable, Enumerator)) and v.value) else str(v) for v in val]
        value = eval("".join(x))
    except:
        try:
            value = eval("".join(val))
        except:
            value = val

    return value


# Holds a name
class Name:
    def __init__(self, parent_block, name=""):
        if name:
            ASSERT_ISVALID(name)
        self.parent = parent_block
        # come up with an unique name if none given
        uniqueId = "__unnamed_" + self.__class__.__name__.lower() + "_" + uuid.uuid4().hex[:8]
        parentName = "" if self.parent == None else self.parent.full_name
        self.name = uniqueId if (not name and self.parent != None) else name
        self.full_name = parentName + ("" if not self.name else "::" + self.name)
        self.parser_file = CurrentFile()
        self.parser_line = CurrentLine()

    def Name(self):
        return self.full_name

    def ShortName(self):
        return self.name


# Holds compound statements and composite types
class Block(Name):
    def __init__(self, parent_block, name=""):
        Name.__init__(self, parent_block, name)
        self.vars = []
        self.enums = []
        self.typedefs = []
        self.classes = []
        self.unions = []
        self.parser_file = CurrentFile()
        self.parser_line = CurrentLine()


# Holds namespaces
class Namespace(Block):
    def __init__(self, parent_block, name=""):
        Block.__init__(self, parent_block, name)
        self.namespaces = []
        self.methods = []
        self.omit = False
        self.stub = -False
        if self.parent != None:                                                                 # case for global namespace
            if isinstance(self.parent, Namespace):
                self.parent.namespaces.append(self)
            else:
                raise ParserError("can't insert namespace '%s' into non-namespace block '%s'" %
                                  (self.name, self.parent.name))

    def Proto(self):
        return self.name

    def __str__(self):
        return "namespace " + self.Proto()

    def __repr__(self):
        return self.__str__() if self.Proto else "global namespace"


# Holds a generic type, wraps fundamental and user-defined types with references and pointers
class Type:
    def __init__(self, basetype):
        self.type = basetype
        self.ref = Ref.VALUE

    def IsConst(self):
        return self.ref & Ref.CONST != 0

    def IsVolatile(self):
        return self.ref & Ref.VOLATILE != 0

    def IsPointer(self):
        return self.ref & Ref.POINTER != 0

    def IsConstPointer(self):
        return self.ref & Ref.CONST_POINTER != 0

    def IsVolatilePointer(self):
        return self.ref & Ref.VOLATILE_POINTER != 0

    def IsReference(self):
        return self.ref & Ref.REFERENCE != 0

    def IsRvalueReference(self):
        return self.ref & Ref.RVALUE_REFERENCE

    def IsPointerToConst(self):
        return self.IsConst() and self.IsPointer()

    def IsConstPointerToConst(self):
        return self.IsConst() and self.IsConstPointer()

    def IsConstReference(self):
        return self.IsConst() and self.IsReference()

    def IsValue(self):
        return not self.IsPointer() and not self.IsReference() and not self.IsRvalueReference()

    def IsNonConstReference(self):
        return self.IsReference() and not self.IsConst()

    def IsNonConstPointer(self):
        return self.IsPointer() and not self.IsConst()

    def IsFundamental(self):
        return isinstance(self.type, Fundamental)

    def IsClass(self):
        return isinstance(self.type, Class)

    def Type(self):
        return self.type

    def TypeName(self):
        return self.type.Proto()

    def CVString(self):
        str = "const" if self.IsConst() else ""
        str += " " if self.IsConst() and self.IsVolatile() else ""
        str += "volatile" if self.IsVolatile() else ""

    def Proto(self):
        _str = "const " if self.IsConst() else ""
        _str += self.TypeName()
        _str += "*" if self.IsPointer() else ""
        _str += "&" if self.IsReference() else "&&" if self.IsRvalueReference() else ""
        _str += " const" if self.IsConstPointer() else ""
        _str += " volatile" if self.IsVolatilePointer() else ""
        return _str

    def __str__(self):
        return self.Proto()

    def __repr__(self):
        return "type " + str(self)


def TypeStr(s):
    return "%s%s" % ("__ERROR_TYPE" if not isinstance(s, Type) else "", str(s))


def ValueStr(s):
    return "%s%s" % ("__ERROR_EXPR" if not isinstance(s, int) and not isinstance(s, str) else "", str(s))


# Holds typedef definition
class Typedef(Identifier, Name):
    def __init__(self, parent_block, string):
        Identifier.__init__(self, parent_block, self, string, [])
        Name.__init__(self, parent_block, self.name)
        self.parent = parent_block
        self.parent.typedefs.append(self)
        self.is_event = False

    def Proto(self):
        return self.full_name

    def __str__(self):
        return "typedef %s %s" % (self.full_name, TypeStr(self.type))

    def __repr__(self):
        return "typedef %s [= %s]" % (self.full_name, TypeStr(self.type.type))


# Holds structs and classes
class Class(Identifier, Block):
    def __init__(self, parent_block, name):
        Identifier.__init__(self, parent_block, self, [name], [])
        Block.__init__(self, parent_block, name)
        self.type = self.full_name
        self.specifiers = []
        self.methods = []
        self.classes = []
        self.ancestors = [] # parent classes
        self._current_access = "public"
        self.omit = False
        self.stub = False
        self.is_json = False
        self.is_event = False
        self.type_name = name
        self.parent.classes.append(self)

    def Proto(self):
        return self.full_name

    def __str__(self):
        return "class " + self.Proto()

    def __repr__(self):
        astr = ""
        if self.ancestors:
            astr = " [<- " + ", ".join(str(a[0]) for a in self.ancestors) + "]"
        return "class %s%s" % (self.full_name, astr)


# Holds unions
class Union(Identifier, Block):
    def __init__(self, parent_block, name):
        Identifier.__init__(self, parent_block, self, [name], [])
        Block.__init__(self, parent_block, name)
        self.methods = []
        self.classes = []
        self._current_access = "public"
        self.omit = False
        self.stub = False
        self.parent.unions.append(self)

    def __str__(self):
        return "union " + self.full_name

    def __repr__(self):
        return str(self)


# Holds enumeration blocks, including class enums
class Enum(Identifier, Block):
    def __init__(self, parent_block, name, is_scoped, type="int"):
        Identifier.__init__(self, parent_block, self, [type, name], [])
        Block.__init__(self, parent_block, name)
        self.items = []
        self.scoped = is_scoped
        self.parent.enums.append(self)
        self._last_value = 0 # used for auto-incrementation

    def Proto(self):
        return self.full_name

    def __str__(self):
        _str = ("enum " if not self.scoped else "enum class ")
        _str += "%s : %s" % (self.Proto(), TypeStr(self.type))
        return _str

    def __repr__(self):
        return str(self)

    def SetValue(self, value):
        self._last_value = value + 1

    def GetValue(self):
        return self._last_value


# Holds functions
class Function(Block, Name):
    def __init__(self, parent_block, name, ret_type, valid_specifiers=["static", "extern", "inline"]):
        self.specifiers = []
        Block.__init__(self, parent_block, name if name else self.name)
        Name.__init__(self, parent_block, self.name)
        self.retval = Identifier(parent_block, self, ret_type, valid_specifiers, False)
        self.omit = False
        self.stub = False
        self.parent.methods.append(self)

    def Proto(self):
        _str = "static " if self.IsStatic() else ""
        _str += TypeStr(self.retval.type) if self.retval.type else ""
        _str += (" " if str(self.retval) else "") + self.name
        _str += "(%s)" % (", ".join([str(v) for v in self.vars]))
        return _str

    def __str__(self):
        return self.Proto()

    def __repr__(self):
        return "function %s" % (self.name)


# Holds variables and constants
class Variable(Identifier, Name):
    def __init__(self, parent_block, string, value=[], valid_specifiers=["static", "extern", "register"]):
        Identifier.__init__(self, parent_block, self, string, valid_specifiers)
        Name.__init__(self, parent_block, self.name)
        self.value = Evaluate(value) if value else None
        self.parent.vars.append(self)

    def Proto(self):
        return "%s %s" % (TypeStr(self.type), self.name)

    def __str__(self):
        return self.Proto()

    def __repr__(self):
        value = ValueStr(self.value) if self.value else None
        return "variable %s %s '%s'%s" % (str(self.specifiers), TypeStr(self.type), str(self.name),
                                          (" = " + value) if value else "")


class Parameter(Variable):
    def __init__(self, parent_block, string, value=[], valid_specifiers=[]):
        Variable.__init__(self, parent_block, string, value, valid_specifiers)
        if self.name in parent_block.retval.meta.param:
            self.meta.brief = parent_block.retval.meta.param[self.name]

    def Proto(self):
        return TypeStr(self.type)

    def __str__(self):
        return "%s %s" % (self.Proto(), self.name)

    def __repr__(self):
        value = ValueStr(self.value) if self.value else None
        return "param %s '%s'%s" % (TypeStr(self.type), str(self.name), (" = " + value) if value else "")


# Holds member attributes
class Method(Function):
    def __init__(self, parent_block, name, ret_type):
        Function.__init__(self, parent_block, name, ret_type,
                          ["inline", "static", "virtual", "explicit", "constexpr", "friend"])
        self.access = self.parent._current_access
        self.qualifiers = []

    def IsVirtual(self):
        return "virtual" in self.specifiers

    def IsPureVirtual(self):
        return "pure-virtual" in self.specifiers

    def IsConst(self):
        return "const" in self.qualifiers

    def IsVolatile(self):
        return "volatile" in self.qualifiers

    def IsStatic(self):
        return "static" in self.specifiers

    def CVString(self):
        str = "const" if self.IsConst() else ""
        str += " " if self.IsConst() and self.IsVolatile() else ""
        str += "volatile" if self.IsVolatile() else ""
        return str

    def Proto(self):
        _str = "virtual " if self.IsVirtual() else ""
        _str += "static " if self.IsStatic() else ""
        _str += TypeStr(self.retval.type) if self.retval.type else ""
        _str += (" " if str(self.retval) else "") + self.name
        _str += "(%s)" % (", ".join([v.Proto() for v in self.vars]))
        _str += " " + self.CVString() if self.CVString() else ""
        _str += " = 0" if self.IsPureVirtual() else ""
        return _str

    def __str__(self):
        return self.Proto()

    def __repr__(self):
        cv = " " + self.CVString() if self.CVString() else ""
        return "method %s %s '%s' (%s)%s %s" % (self.access, TypeStr(self.type), self.name, ", ".join(
            [str(v) for v in self.vars]), cv, str(self.specifiers))


class Destructor(Method):
    def __init__(self, parent_block, name, ret_type):
        Method.__init__(self, parent_block, name, ret_type)

    def __repr__(self):
        return "destructor %s '%s' %s" % (self.access, self.name, str(self.specifiers))


# Holds member attributes and constants
class Attribute(Variable):
    def __init__(self, parent_block, string, value=[]):
        Variable.__init__(self, parent_block, string, value, ["static", "constexpr", "thread_local", "mutable"])
        self.access = self.parent._current_access

    def IsConst(self):
        return "const" in self.qualifiers

    def IsVolatile(self):
        return "volatile" in self.qualifiers

    def IsStatic(self):
        return "static" in self.specifiers

    def CVString(self):
        str = "const" if self.IsConst() else ""
        str += " " if self.IsConst() and self.IsVolatile() else ""
        str += "volatile" if self.IsVolatile() else ""
        return str

    def Proto(self):
        return "%s %s" % (TypeStr(self.type), str(self.name))

    def __str__(self):
        value = ValueStr(self.value) if self.value else None
        return "%s %s" % (self.Proto(), (" = " + value) if value else "")

    def __repr__(self):
        value = ValueStr(self.value) if self.value else None
        return "attribute %s %s %s '%s'%s" % (self.access, str(self.specifiers), TypeStr(self.type), str(self.name),
                                              (" = " + value) if value else "")


# Holds enumeration items
class Enumerator(Identifier, Name):
    def __init__(self, parent_block, name, value=None, type=["int"]):
        parent_enum = parent_block if parent_block.scoped else parent_block.parent
        Identifier.__init__(self, parent_enum, self, [type, name], [])
        Name.__init__(self, parent_enum, self.name)
        self.parent = parent_block
        self.value = parent_block.GetValue() if value == None else Evaluate(value)
        if isinstance(self.value, (int)):
            self.parent.SetValue(self.value)
        self.parent.items.append(self)

    def Proto(self):
        return self.name

    def __str__(self):
        return "%s = %s" % (self.Proto(), ValueStr(self.value))

    def __repr__(self):
        return "enumerator %s '%s' = %s" % (TypeStr(self.type), str(self.full_name), ValueStr(self.value))


class TemplateNonTypeParameter(Variable):
    def __init__(self, parent_block, string, index, value=[]):
        Variable.__init__(self, parent_block, string, [])
        self.value = Evaluate(value) if value else None
        self.parent.arguments.append(self)
        self.index = index

    def __repr__(self):
        return "non-type parameter %s '%s' [= %s]" % (TypeStr(self.type), self.name, str(self.value))


class TemplateTypeParameter(Name):
    def __init__(self, parent_block, string, index):
        Name.__init__(self, parent_block, string)
        parent_block.parameters.append(self)
        self.index = index

    def Proto(self):
        return self.name

    def __str__(self):
        return "typename %s" % self.Proto()

    def __repr__(self):
        return "type parameter %s" % self.Proto()


class InstantiatedTemplateClass(Class):
    def __init__(self, parent_block, name, params, args):
        hash = hashlib.sha1("_".join(args).encode('utf-8')).hexdigest()[:8].upper()
        Class.__init__(self, parent_block, name + "Instance" + hash)
        self.baseName = Name(parent_block, name)
        self.params = params
        self.args = args
        self.type = self.TypeName()

    def TypeName(self):
        return "%s<%s>" % (self.baseName.full_name, ", ".join([str(p) for p in self.args]))

    def Proto(self):
        return self.TypeName()

    def __str__(self):
        s = []
        for i, _ in enumerate(self.params):
            s.append(self.params[i].name + " = " + str(self.args[i]))
        _str = "template class %s<%s>" % (self.baseName.full_name, ", ".join([str(p) for p in self.params]))
        _str += " [with %s]" % (", ".join(s))
        return _str

    def __repr__(self):
        s = []
        for i, _ in enumerate(self.params):
            s.append(self.params[i].name + " = " + str(self.args[i]))
        return "%s [instance of %s [with %s]]" % (Class.__repr__(self), self.TypeName(), ", ".join(s))


class TemplateClass(Class):
    def ParseArguments(self, string):
        groups = re.findall(r'<(?:[^<>]*|<[^<>]*>)*>', string)
        if groups:
            stringParams = [s.strip() for s in re.split(r',\s*(?![^<>]*\))', groups[0][1:-1].strip())]
            return stringParams
        else:
            return []

    def __init__(self, parent_block, name, params):
        Class.__init__(self, parent_block, name)
        self.parameters = []
        self.arguments = []
        self.paramList = []
        paramList = self.ParseArguments(params)
        for p in paramList:
            if "typename" in p or "class" in p:
                param = TemplateTypeParameter(self, p.split()[1], index=paramList.index(p))
            else:
                param = TemplateNonTypeParameter(self, p.split(), index=paramList.index(p))
            self.paramList.append(param)

    def Instantiate(self, arguments):
        def _Substitute(identifier):
            if isinstance(identifier.type, list):
                for i, v in enumerate(identifier.type):
                    if isinstance(v, TemplateTypeParameter):
                        if v.name in paramDict:
                            identifier.type[i] = strArgs[paramDict[v.name].index]
                            identifier.ResolveIdentifiers(instance)
                            break
            if (isinstance(identifier, Enumerator) or isinstance(identifier, Variable)) and identifier.value:
                for i, v in enumerate(identifier.value):
                    if isinstance(v, TemplateNonTypeParameter):
                        identifier.value[i] = strArgs[argDict[v.name].index]
                        identifier.value = Evaluate(identifier.value)
                        break

        strArgs = self.ParseArguments(arguments)
        paramDict = dict(zip([x.name for x in self.parameters], self.parameters))
        argDict = dict(zip([x.name for x in self.arguments], self.arguments))
        instance = InstantiatedTemplateClass(self.parent, self.name, self.paramList, strArgs)
        instance.ancestors = self.ancestors
        instance.specifiers = self.specifiers
        instance.is_json = self.is_json
        instance.is_event = self.is_event

        for t in self.typedefs:
            newTypedef = copy.copy(t)
            newTypedef.parent = instance
            newTypedef.type = copy.copy(t.type)
            _Substitute(newTypedef)
            instance.typedefs.append(newTypedef)

        for v in self.vars:
            newAttr = copy.copy(v)
            newAttr.parent = instance
            newAttr.type = copy.copy(v.type)
            newAttr.value = copy.copy(v.value)
            _Substitute(newAttr)
            instance.typedefs.append(newAttr)

        for e in self.enums:
            newEnum = copy.copy(e)
            newEnum.items = []
            newEnum.parent = instance
            _Substitute(newEnum)
            for i in e.items:
                newItem = copy.copy(i)
                newItem.type = copy.copy(i.type)
                newItem.value = copy.copy(i.value)
                _Substitute(newItem)
                newEnum.items.append(newItem)
            instance.enums.append(newEnum)

        for m in self.methods:
            newMethod = copy.copy(m)
            newMethod.vars = []
            newMethod.parent = instance
            if not isinstance(m, Destructor):
                newMethod.retval = copy.copy(m.retval)
                newMethod.retval.type = copy.copy(m.retval.type)
                _Substitute(newMethod.retval)
                for p in m.vars:
                    newVar = copy.copy(p)
                    newVar.type = copy.copy(p.type)
                    newVar.value = copy.copy(p.value)
                    _Substitute(newVar)
                    newMethod.vars.append(newVar)
            instance.methods.append(newMethod)

        return instance

    def __str__(self):
        return "template class %s<%s>" % (self.full_name, ", ".join([str(p) for p in self.paramList]))

    def __repr__(self):
        return "template %s<%s>" % (Class.__repr__(self), ", ".join([repr(p) for p in self.paramList]))


# -------------------------------------------------------------------------
# PRIVATE FUNCTIONS
# -------------------------------------------------------------------------


# Source file test into a list of tokens, removing comments and preprocessor directives.
def __Tokenize(contents):
    global current_file
    global current_line

    tokens = [s.strip() for s in re.split(r"([\r\n])", contents, flags=re.MULTILINE) if s]
    eoltokens = []
    line = 1
    for token in tokens:
        if token.startswith("// @file:"):
            line = 1
        if token == '':
            eoltokens.append("// @line:" + str(line) + " ")
            line = line + 1
        elif (len(eoltokens) > 1) and eoltokens[-2].endswith("\\"):
            del eoltokens[-1]
            eoltokens[-1] = eoltokens[-1][:-1] + token
        else:
            eoltokens.append(token)

    contents = "\n".join(eoltokens)

    formula = (
        r"(#if 0[\S\s]*?#endif)"
        r"|(#.*)"                                                       # preprocessor
        r"|(/\*[\S\s]*?\*/)"                                            # multi-line comments
        r"|(//.*)"                                                      # single line comments
        r"|(\"[^\"]+\")"                                                # double quotes
        r"|(\'[^\']+\')"                                                # quotes
        r"|(::)|(==)|(!=)|(>=)|(<=)|(&&)|(\|\|)"                        # two-char operators
        r"|(\+\+)|(--)|(\+=)|(-=)|(/=)|(\*=)|(%=)|(^=)|(&=)|(\|=)|(~=)"
        r"|([,:;~!?=^/*%-\+&<>\{\}\(\)\[\]])"                           # single-char operators
        r"|([\r\n\t ])"                                                 # whitespace
    )

    tokens = [s.strip() for s in re.split(formula, contents, flags=(re.MULTILINE)) if s]

    tagtokens = []
    # check for special metadata within comments
    skipmode = False
    for token in tokens:
        if token:
            if skipmode:
                if "@file" in token:
                    skipmode = False
                else:
                    continue

            def __ParseLength(string, tag):
                formula = (r"(\"[^\"]+\")"
                           r"|(\'[^\']+\')"
                           r"|(\*/)|(::)|(==)|(!=)|(>=)|(<=)|(&&)|(\|\|)"
                           r"|(\+\+)|(--)|(\+=)|(-=)|(/=)|(\*=)|(%=)|(^=)|(&=)|(\|=)|(~=)"
                           r"|([,:;~!?=^/*%-\+&<>\{\}\(\)\[\]])"
                           r"|([\r\n\t ])")
                tagtokens.append(tag.upper())
                length_str = string[string.index(tag) + len(tag):]
                length_tokens = [
                    s.strip() for s in re.split(formula, length_str, flags=re.MULTILINE)
                    if isinstance(s, str) and len(s.strip())
                ]
                if length_tokens[0] == ':':
                    length_tokens = length_tokens[1:]
                no_close_last = (length_tokens[0] == '(')
                tokens = []
                par_count = 0
                for t in length_tokens:
                    if t == '(':
                        if tokens:
                            tokens.append(t)
                        par_count += 1
                    elif t == ')':
                        par_count -= 1
                        if par_count == 0:
                            if not no_close_last:
                                tokens.append(t)
                            break
                        else:
                            tokens.append(t)
                    elif t == '*/' or t == "," or t[0] == '@':
                        break
                    else:
                        tokens.append(t)
                        if par_count == 0:
                            break
                if par_count != 0:
                    raise ParserError("unmatched parenthesis in %s expression" % tag)
                if len(tokens) == 0:
                    raise ParserError("invalid %s value" % tag)
                return tokens

            if ((token[:2] == "/*") and (token.count("/*") != token.count("*/"))):
                raise ParserError("multi-line comment not closed")

            if ((token[:2] == "/*") or (token[:2] == "//")):

                def _find(word, string):
                    return re.compile(r"[ \r\n/\*]({0})([: \r\n\*]|$)".format(word)).search(string) != None

                if _find("@stubgen", token):
                    if "@stubgen:skip" in token:
                        skipmode = True
                    elif "@stubgen:omit" in token:
                        tagtokens.append("@OMIT")
                    elif "@stubgen:stub" in token:
                        tagtokens.append("@STUB")
                    elif "@stubgen:include" in token:
                        pass                                   # nothing to do here
                    else:
                        raise ParserError("invalid @stubgen tag")
                if _find("@in", token):
                    tagtokens.append("@IN")
                if _find("@out", token):
                    tagtokens.append("@OUT")
                if _find("@inout", token):
                    tagtokens.append("@IN")
                    tagtokens.append("@OUT")
                if _find("@property", token):
                    tagtokens.append("@PROPERTY")
                if _find("@json", token):
                    tagtokens.append("@JSON")
                if _find("@event", token):
                    tagtokens.append("@EVENT")
                if _find("@brief", token):
                    tagtokens.append("@BRIEF")
                    desc = token[token.index("@brief") + 7:token.index("*/") if "*/" in token else None].strip()
                    if desc:
                        tagtokens.append(desc)
                if _find("@details", token):
                    tagtokens.append("@DETAILS")
                    desc = token[token.index("@details") + 9:token.index("*/") if "*/" in token else None].strip()
                    if desc:
                        tagtokens.append(desc)
                if _find("@param", token):
                    tagtokens.append("@PARAM")
                    idx = token.index("@param")
                    paramName = token[idx + 7:].split()[0]
                    paramDesc = token[token.index(paramName) +
                                      len(paramName):token.index("*/") if "*/" in token else None].strip()
                    if paramName and paramDesc:
                        tagtokens.append(paramName)
                        tagtokens.append(paramDesc)
                if _find("@retval", token):
                    tagtokens.append("@RETVAL")
                    idx = token.index("@retval")
                    errorName = token[idx + 8:].split()[0]
                    errorDesc = token[token.index(errorName) +
                                      len(errorName):token.index("*/") if "*/" in token else None].strip()
                    if errorName and errorDesc:
                        tagtokens.append(errorName)
                        tagtokens.append(errorDesc)
                if _find("@length", token):
                    tagtokens.append(__ParseLength(token, "@length"))
                if _find("@maxlength", token):
                    tagtokens.append(__ParseLength(token, "@maxlength"))
                if _find("@interface", token):
                    tagtokens.append(__ParseLength(token, "@interface"))
                if _find("@file", token):
                    idx = token.index("@file:") + 6
                    tagtokens.append("@FILE:" + token[idx:])
                    current_file = token[idx:]
                if _find("@line", token):
                    idx = token.index("@line:") + 6
                    if len(tagtokens) and tagtokens[-1].startswith("@LINE:"):
                        del tagtokens[-1]
                    current_line = int(token[idx:].split()[0])
                    tagtokens.append("@LINE:" + token[idx:])
            elif len(token) > 0 and token[0] != '#' and token != "EXTERNAL":
                tagtokens.append(token)

    tagtokens.append(";") # prevent potential out-of-range errors

    return tagtokens


# -------------------------------------------------------------------------
# EXPORTED FUNCTIONS
# -------------------------------------------------------------------------

i = 0
tokens = []
line_numbers = []
files = []
current_line = 0
current_file = "undefined"


def CurrentFile():
    if i > 0:
        # error during c++ parsing
        return files[i]
    else:
        # error during preprocessing
        return current_file


def CurrentLine():
    if i > 0:
        # error during c++ parsing
        return line_numbers[i]
    else:
        # error during preprocessing
        return current_line


# Builds a syntax tree (data structures only) of C++ source code
def Parse(contents):
    # Start in global namespace.
    global global_namespace
    global current_file
    global tokens
    global line_numbers
    global files
    global i

    i = 0
    tokens = []
    line_numbers = []
    files = []
    line_tokens = []
    current_line = 0
    current_file = "undefined"

    # Split into tokens first
    line_tokens = __Tokenize(contents)

    for token in line_tokens:
        if isinstance(token, str) and token.startswith("@LINE:"):
            current_line = int(token[6:].split()[0])
        elif isinstance(token, str) and token.startswith("@FILE:"):
            current_file = token[6:]
            append = False
        else:
            tokens.append(token)
            line_numbers.append(current_line)
            files.append(current_file)

    global_namespace = Namespace(None)
    current_block = [global_namespace]
    next_block = None
    last_template_def = []
    min_index = 0
    omit_next = False
    stub_next = False
    json_next = False
    event_next = False
    in_typedef = False

    # Main loop.
    while i < len(tokens):
        # Handle special tokens
        if not isinstance(tokens[i], str):
            i = i + 1
            continue

        if tokens[i] == "@OMIT":
            omit_next = True
            tokens[i] = ";"
            i += 1
        elif tokens[i] == "@STUB":
            stub_next = True
            tokens[i] = ";"
            i += 1
        elif tokens[i] == "@JSON":
            json_next = True
            tokens[i] = ";"
            i += 1
        elif tokens[i] == "@EVENT":
            event_next = True
            tokens[i] = ";"
            i += 1

        # Swallow template definitions
        elif tokens[i] == "template" and tokens[i + 1] == '<':
            s = i
            i += 1
            nest = 0
            while True:
                if tokens[i] == ">":
                    if nest == 1:
                        break
                    nest -= 1
                elif tokens[i] == "<":
                    nest += 1
                i += 1
            i += 1
            last_template_def = tokens[s:i]
            min_index = i

        # Parse namespace definition...
        elif tokens[i] == "namespace":
            namespace_name = ""
            if is_valid(tokens[i + 1]): # is there a namespace name?
                namespace_name = tokens[i + 1]
                i += 1
            next_block = Namespace(current_block[-1], namespace_name)
            i += 1

        # Parse type alias...
        elif isinstance(current_block[-1], (Namespace, Class)) and tokens[i] == "typedef":
            j = i + 1
            while tokens[j] != ";":
                j += 1
            typedef = Typedef(current_block[-1], tokens[i + 1:j])
            if event_next:
                typedef.is_event = True
                event_next = False
            if not isinstance(typedef.type, Type) and typedef.type[0] == "enum":
                in_typedef = True
                i += 1
            else:
                i = j + 1

        # Parse "using"...
        elif isinstance(current_block[-1], (Namespace, Class)) and tokens[i] == "using":
            if tokens[i + 1] != "namespace" and tokens[i + 2] == "=":
                i += 2
                j = i + 1
                while tokens[j] != ";":
                    j += 1
                # reuse typedef class but correct name accordingly
                if not current_block[-1].omit:
                    typedef = Typedef(current_block[-1], tokens[i + 1:j])
                    if event_next:
                        typedef.is_event = True
                        event_next = False
                    typedef_id = Name(current_block[-1], tokens[i - 1])
                    typedef.name = typedef_id.name
                    typedef.full_name = typedef_id.full_name
                i = j + 1
            elif tokens[i + 1] != "namespace" and tokens[i + 2] != "=":
                if not current_block[-1].omit:
                    raise ParserError("using-declarations are not supported")
            elif tokens[i + 1] == "namespace":
                if not current_block[-1].omit:
                    raise ParserError("'using namespace' directives are not supported")

        # Parse class definition...
        elif (tokens[i] == "class") or (tokens[i] == "struct") or (tokens[i] == "union"):
            name = tokens[i + 1]
            if tokens[i] == "union":
                new_class = Union(current_block[-1], name)
            else:
                new_class = None
                # Look up previous classes in case there already was a forward declaration of this class
                for idx, cl in enumerate(current_block[-1].classes):
                    if cl.name == name:
                        if len(cl.methods) == 0:
                            new_class = cl
                            # move the class to to bottom
                            current_block[-1].classes.append(new_class)
                            del current_block[-1].classes[idx]
                        else:
                            raise ParserError("duplicate class name: " + cl.name)
                        break

                if new_class == None:
                    if last_template_def:
                        new_class = TemplateClass(current_block[-1], name, " ".join(last_template_def))
                        last_template_def = []
                    else:
                        new_class = Class(current_block[-1], name)

            new_class._current_access = "private" if tokens[i] == "class" else "public"

            if omit_next:
                new_class.omit = True
                omit_next = False
            elif stub_next:
                new_class.stub = True
                stub_next = False
            if json_next:
                new_class.is_json = True
                json_next = False
            if event_next:
                new_class.is_event = True
                event_next = False

            if last_template_def:
                new_class.specifiers.append(" ".join(last_template_def))
                last_template_def = []

            i += 1
            if tokens[i + 1] == "final":
                new_class.specifiers.append(tokens[i + 2])
                i += 1

            # parse class ancestors...
            # TODO: refactor!!
            if tokens[i + 1] == ':':
                i += 1
                parent_class = ""
                parent_access = "private"
                specifiers = []
                while True:
                    if tokens[i + 1] in ['{', ',']:
                        # try to find a reference to an already found type
                        parent_ref = Identifier(current_block[-1], current_block[-1], [parent_class], [])
                        new_class.ancestors.append([parent_ref.type, parent_access, specifiers])
                        parent_access = "private"
                        if tokens[i + 1] == '{':
                            break
                    elif tokens[i + 1] in ["public", "private", "protected"]:
                        parent_access = tokens[i + 1]
                    elif tokens[i + 1] == "virtual":
                        specifiers.append(tokens[i + 1])
                    else:
                        parent_class += tokens[i + 1]
                    i += 1

            i += 1
            if tokens[i] == ';':
                i += 1
            else:
                next_block = new_class

        # Parse enum definition...
        elif isinstance(current_block[-1], (Namespace, Class)) and tokens[i] == "enum":
            enum_name = ""
            enum_type = "int"
            is_scoped = False
            if (tokens[i + 1] == "class") or (tokens[i + 1] == "struct"):
                is_scoped = True
                i += 1
            if is_valid(tokens[i + 1]): # enum name given?
                enum_name = tokens[i + 1]
                i += 1
            if tokens[i + 1] == ':':
                enum_type = tokens[i + 2]
                i += 2

            new_enum = Enum(current_block[-1], enum_name, is_scoped, enum_type)
            next_block = new_enum
            i += 1

        # Parse class access specifier...
        elif isinstance(current_block[-1], Class) and tokens[i] == ':':
            current_block[-1]._current_access = tokens[i - 1]
            ASSERT_ISEXPECTED(current_block[-1]._current_access, ["private", "protected", "public"])
            i += 1

        # Parse function/method definition...
        elif isinstance(current_block[-1], (Namespace, Class)) and tokens[i] == "(":
            # concatenate tokens to handle operators and destructors
            j = i - 1
            k = i - 1
            if isinstance(current_block[-1], Class) and (tokens[i - 2] == "operator"):
                name = "operator" + tokens[i - 1]
                j -= 1
                k -= 1
            else:
                name = tokens[i - 1]
                if tokens[i - 2] == '~':
                    name = "~" + name #dtor
                    j -= 1
                    k -= 1

            # locate return value
            while j >= min_index and tokens[j] not in ['{', '}', ';', ':']:
                j -= 1
            if not current_block[-1].omit and not omit_next:
                ret_type = tokens[j + 1:k]
            else:
                ret_type = []

            if isinstance(current_block[-1], Class):
                if name[0] == "~":
                    method = Destructor(current_block[-1], name, ret_type)
                else:
                    method = Method(current_block[-1], name, ret_type)
            else:
                method = Function(current_block[-1], name, ret_type)

            if omit_next:
                method.omit = True
                omit_next = False
            elif method.parent.omit:
                method.omit = True
            elif stub_next:
                method.stub = True
                stub_next = False
            elif method.parent.stub:
                method.stub = True

            if last_template_def:
                method.specifiers.append(" ".join(last_template_def))
                last_template_def = []

            # try to detect a function/macro call
            function_call = not ret_type and ((name != current_block[-1].name) and (name !=
                                                                                    ("~" + current_block[-1].name)))

            # parse method parameters...
            j = i
            nest = 0
            nest2 = 0
            while tokens[i] != ')':
                while tokens[j]:
                    if tokens[j] == '(':
                        nest += 1
                    elif tokens[j] == ')':
                        nest -= 1
                        if nest == 0 and nest2 == 0:
                            break
                    if tokens[j] == '<':
                        nest2 += 1
                    elif tokens[j] == '>':
                        nest2 -= 1
                    elif tokens[j] == ',' and nest == 1 and nest2 == 0:
                        break

                    j += 1

                param = tokens[i + 1:j]
                if len(param) and not (len(param) == 1 and param[0] == "void"): # remove C-style f(void)
                    value = []
                    if '=' in param:
                        assignment = param.index('=')
                        value = param[assignment + 1:]
                        param = param[0:assignment]
                    if not current_block[-1].omit and not method.omit:
                        Parameter(method, param, value)
                i = j
                j += 1

            if nest:
                raise ParserError("unmatched parenthesis '('")
            if nest2:
                raise ParserError("unmatched parenthesis '<'")

            # parse post-declaration qualifiers/specifiers...
            if isinstance(current_block[-1], Class):
                while tokens[i] not in [';', '{', ':']:
                    # const, volatile
                    if tokens[i] in ["const", "volatile"]:
                        method.qualifiers.append(tokens[i])
                    # handle pure virtual methods
                    elif (tokens[i] == "="):
                        if tokens[i + 1] == "0" and "virtual" in method.specifiers: # mark the virtual function as pure
                            method.specifiers.append("pure-virtual")
                        elif tokens[i + 1] in ["default", "delete"]:
                            method.specifiers.append(tokens[i + 1])
                        i += 1
                    elif tokens[i] in ["override", "final", "noexcept"]:
                        method.specifiers.append(tokens[i])
                    i += 1

            if function_call: # it was apparently a function call and not declaration, so remove it
                current_block[-1].methods.pop()
            else:
                next_block = method

            if tokens[i] == ';':
                i += 1
            elif tokens[i] == ':': # skip ctor initializers
                while tokens[i] != '{':
                    i += 1

        # Handle opening a compound block or a composite type
        elif tokens[i] == '{':
            current_block.append(next_block)
            i += 1

        # Handle closing a compound block/composite type
        elif tokens[i] == '}':
            if isinstance(current_block[-1], Class) and (tokens[i + 1] != ';'):
                raise ParserError("definitions following a class declaration is not supported (%s)" %
                                  current_block[-1].full_name)
            if len(current_block) > 1:
                current_block.pop()
            else:
                raise ParserError("unmatched brace '{'")
            i += 1
            next_block = Block(current_block[-1]) # new anonymous scope

        # Parse variables and member attributes
        elif isinstance(current_block[-1],
                        (Namespace, Class)) and tokens[i] == ';' and (is_valid(tokens[i - 1]) or tokens[i - 1] == "]"):
            j = i - 1
            while j >= min_index and tokens[j] not in ['{', '}', ';', ":"]:
                j -= 1
            if not current_block[-1].omit:
                if isinstance(current_block[-1], Class):
                    Attribute(current_block[-1], tokens[j + 1:i])
                else:
                    Variable(current_block[-1], tokens[j + 1:i])
            i += 1

        # Parse constants and member constants
        elif isinstance(current_block[-1], (Namespace, Class)) and (tokens[i] == '=') and (tokens[i - 1] != "operator"):
            j = i - 1
            k = i + 1
            while tokens[j] not in ['{', '}', ';', ":"]:
                j -= 1
            while tokens[k] != ';':
                k += 1
            if not current_block[-1].omit:
                if isinstance(current_block[-1], Class):
                    Attribute(current_block[-1], tokens[j + 1:i], tokens[i + 1:k])
                else:
                    Variable(current_block[-1], tokens[j + 1:i], tokens[i + 1:k])
            i = k

        # Parse an enum block...
        elif isinstance(current_block[-1], Enum):
            enum = current_block[-1]
            j = i
            while True:
                if tokens[i] in ['}', ',']:
                    Enumerator(enum, tokens[j], tokens[j + 2:i] if tokens[j + 1] == '=' else None, enum.type)
                    if tokens[i + 1] == '}':
                        i += 1 # handle ,} situation
                        break
                    elif tokens[i] == '}':
                        break
                    else:
                        j = i + 1
                i += 1
            if in_typedef:
                current_block[-2].typedefs[-1].type = Type(enum)
                in_typedef = False
        else:
            i += 1

    return global_namespace


# -------------------------------------------------------------------------


def ReadFile(source_file, quiet=False, initial=""):
    contents = initial
    global current_file
    try:
        with open(source_file) as file:
            file_content = file.read()
            idx = file_content.find("@stubgen:include")
            if idx != -1:
                match = re.search(r'\"(.+?)\"', file_content[idx:])
                if match:
                    if match.group(1) != os.path.basename(os.path.realpath(source_file)):
                        prev = current_file
                        current_file = source_file
                        print("including " + match.group(1))
                        contents += ReadFile(
                            os.path.dirname(os.path.realpath(source_file)) + os.sep + match.group(1), contents)
                        current_file = prev
                    else:
                        raise ParserError("can't recursively include file '%s'" % source_file)

            contents += "// @file:%s\n" % source_file
            contents += file_content
            return contents
    except FileNotFoundError:
        if not quiet:
            raise ParserError("failed to open file '%s'" % source_file)
        return ""


def ParseFile(source_file):
    contents = ReadFile(source_file)
    return Parse(contents)


def ParseFiles(source_files):
    contents = ""
    for source_file in source_files:
        if source_file:
            quiet = (source_file[0] == "@")
            contents += ReadFile((source_file[1:] if quiet else source_file), quiet, "")
    return Parse(contents)


# -------------------------------------------------------------------------


def DumpTree(tree, ind=0):
    indent = ind * " "

    if isinstance(tree, (Namespace, Class)):
        print(indent + str(tree))

        for td in tree.typedefs:
            print(indent + 2 * " " + str(td))

        for e in tree.enums:
            print(indent + 2 * " " + str(e))
            for item in e.items:
                print(indent + 4 * " " + str(item))

    for v in tree.vars:
        print(indent + 2 * " " + str(v))

    if isinstance(tree, (Namespace, Class)):
        for m in tree.methods:
            print(indent + 2 * " " + str(m))

    if isinstance(tree, (Namespace, Class)):
        for c in tree.classes:
            DumpTree(c, ind + 2)

    if isinstance(tree, Namespace):
        for n in tree.namespaces:
            DumpTree(n, ind + 2)


# -------------------------------------------------------------------------
# entry point

if __name__ == "__main__":
    tree = ParseFile(sys.argv[1])
    if isinstance(tree, Namespace):
        DumpTree(tree)
    else:
        print(tree)

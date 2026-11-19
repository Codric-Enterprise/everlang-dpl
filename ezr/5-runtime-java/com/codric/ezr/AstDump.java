package com.codric.ezr;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Prints this front end's verdict on each source file, in the forge's
 * canonical form (7-forge/contract.py), so tests/parser_agreement.py can
 * hold the Java parser to the same golden corpus as the Python ones.
 *
 * <pre>
 *   java -cp out com.codric.ezr.AstDump FILE...
 * </pre>
 *
 * One line per file: the path, a tab, then either the s-expression of the
 * accepted program or {@code FAIL[lex|parse/defect]}. Java's refusals carry
 * a defect but no character offset, so the position is not part of what
 * this witness reports; the Python side compares it among the witnesses
 * that do report one.
 */
public final class AstDump {

    private AstDump() { }

    public static void main(String[] args) throws IOException {
        StringBuilder out = new StringBuilder();
        for (String arg : args) {
            String src = Files.readString(Path.of(arg), StandardCharsets.UTF_8);
            out.append(arg).append('\t').append(verdict(src)).append('\n');
        }
        System.out.print(out);
    }

    static String verdict(String src) {
        Lexer.Out lexed = Lexer.lex(src);
        if (!lexed.ok()) {
            return "FAIL[lex/" + lexed.error().defect + "]";
        }
        Parser.Out parsed = Parser.parse(src);
        if (parsed.error() != null) {
            return "FAIL[parse/" + parsed.error().defect + "]";
        }
        return sexp(parsed.ast());
    }

    /** Mirrors {@code N.sexp()} in 7-forge/contract.py, node for node. */
    static String sexp(Ast n) {
        return switch (n) {
            case Ast.Num  x -> num(x.value());
            case Ast.Str  x -> "(str " + pyRepr(x.value()) + ")";
            case Ast.Bool x -> x.value() ? "true" : "false";
            case Ast.Var  x -> x.name();
            case Ast.Bin  x -> "(" + x.op() + " " + sexp(x.left()) + " "
                               + sexp(x.right()) + ")";
            case Ast.If   x -> "(if " + sexp(x.cond()) + " " + sexp(x.then())
                               + " " + sexp(x.otherwise()) + ")";
            case Ast.Call x -> "(call " + x.name() + joined(x.args()) + ")";
            case Ast.Def  x -> "(def " + x.name() + " ("
                               + String.join(" ", x.params()) + ") "
                               + sexp(x.body()) + ")";
            case Ast.Let  x -> "(let " + x.name() + " " + sexp(x.value()) + " "
                               + sexp(x.body()) + ")";
            case Ast.Lst  x -> "(list" + joined(x.items()) + ")";
            case Ast.Prog x -> {
                StringBuilder sb = new StringBuilder("(prog");
                for (Ast.Def d : x.defs()) sb.append(' ').append(sexp(d));
                if (x.expr() != null) sb.append(' ').append(sexp(x.expr()));
                yield sb.append(')').toString();
            }
        };
    }

    private static String joined(List<? extends Ast> xs) {
        StringBuilder sb = new StringBuilder();
        for (Ast a : xs) sb.append(' ').append(sexp(a));
        return sb.toString();
    }

    /** contract._num: a whole number prints without a fraction. */
    static String num(double v) {
        if (v == Math.rint(v) && !Double.isInfinite(v) && Math.abs(v) < 1e15) {
            return Long.toString((long) v);
        }
        return Double.toString(v);
    }

    /** Python's repr() of a str, for the characters EZR strings can hold. */
    static String pyRepr(String s) {
        char q = (s.indexOf('\'') >= 0 && s.indexOf('"') < 0) ? '"' : '\'';
        StringBuilder sb = new StringBuilder().append(q);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\t' -> sb.append("\\t");
                case '\r' -> sb.append("\\r");
                default -> {
                    if (c == q) sb.append('\\');
                    sb.append(c);
                }
            }
        }
        return sb.append(q).toString();
    }
}

package com.codric.ezr;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Prints this front end's verdict on each source file, in the forge's canonical form
 * (7-forge/contract.py), so tests/parser_agreement.py can hold the Java parser to the same golden
 * corpus as the Python ones.
 *
 * <pre>
 *   java -cp out com.codric.ezr.AstDump FILE...
 * </pre>
 *
 * <p>One line per file: the path, a tab, then either the s-expression of the accepted program or
 * {@code FAIL[lex|parse/defect]}. Java's refusals carry a defect but no character offset, so the
 * position is not part of what this witness reports; the Python side compares it among the
 * witnesses that do report one.
 */
public final class AstDump {

  /** Below this magnitude a whole double prints exactly as a long. */
  private static final double EXACT_LONG_LIMIT = 1e15;

  private AstDump() {
    // static helpers only
  }

  /**
   * Prints one verdict line per file named on the command line.
   *
   * @param args paths of EZR source files
   * @throws IOException if a file cannot be read
   */
  public static void main(final String[] args) throws IOException {
    StringBuilder out = new StringBuilder();
    for (String arg : args) {
      String src = Files.readString(Path.of(arg), StandardCharsets.UTF_8);
      out.append(arg).append('\t').append(verdict(src)).append('\n');
    }
    System.out.print(out);
  }

  /**
   * Lexes and parses one program.
   *
   * @param src the program text
   * @return the canonical s-expression, or the refusal and its defect
   */
  static String verdict(final String src) {
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

  /**
   * Mirrors {@code N.sexp()} in 7-forge/contract.py, node for node.
   *
   * @param n the node to print
   * @return its canonical s-expression
   */
  static String sexp(final Ast n) {
    if (n instanceof Ast.Num) {
      return num(((Ast.Num) n).value());
    }
    if (n instanceof Ast.Str) {
      return "(str " + pyRepr(((Ast.Str) n).value()) + ")";
    }
    if (n instanceof Ast.Bool) {
      if (((Ast.Bool) n).value()) {
        return "true";
      }
      return "false";
    }
    if (n instanceof Ast.Var) {
      return ((Ast.Var) n).name();
    }
    if (n instanceof Ast.Bin) {
      Ast.Bin x = (Ast.Bin) n;
      return "(" + x.op() + " " + sexp(x.left()) + " " + sexp(x.right()) + ")";
    }
    if (n instanceof Ast.If) {
      Ast.If x = (Ast.If) n;
      return "(if " + sexp(x.cond()) + " " + sexp(x.then()) + " " + sexp(x.otherwise()) + ")";
    }
    if (n instanceof Ast.Call) {
      Ast.Call x = (Ast.Call) n;
      return "(call " + x.name() + joined(x.args()) + ")";
    }
    if (n instanceof Ast.Def) {
      Ast.Def x = (Ast.Def) n;
      return "(def " + x.name() + " (" + String.join(" ", x.params()) + ") " + sexp(x.body()) + ")";
    }
    if (n instanceof Ast.Let) {
      Ast.Let x = (Ast.Let) n;
      return "(let " + x.name() + " " + sexp(x.value()) + " " + sexp(x.body()) + ")";
    }
    if (n instanceof Ast.Lst) {
      return "(list" + joined(((Ast.Lst) n).items()) + ")";
    }
    if (n instanceof Ast.Prog) {
      Ast.Prog x = (Ast.Prog) n;
      StringBuilder sb = new StringBuilder("(prog");
      sb.append(joined(x.defs()));
      if (x.expr() != null) {
        sb.append(' ').append(sexp(x.expr()));
      }
      return sb.append(')').toString();
    }
    throw new IllegalArgumentException("no s-expression for " + n);
  }

  /**
   * Prints each node with a leading space.
   *
   * @param xs the nodes, in order
   * @return the joined s-expressions
   */
  private static String joined(final List<? extends Ast> xs) {
    StringBuilder sb = new StringBuilder();
    for (Ast a : xs) {
      sb.append(' ').append(sexp(a));
    }
    return sb.toString();
  }

  /**
   * Mirrors contract._num: a whole number prints without a fraction.
   *
   * @param v the number
   * @return its canonical spelling
   */
  static String num(final double v) {
    if (v == Math.rint(v) && !Double.isInfinite(v) && Math.abs(v) < EXACT_LONG_LIMIT) {
      return Long.toString((long) v);
    }
    return Double.toString(v);
  }

  /**
   * Python's repr() of a str, for the characters EZR strings can hold.
   *
   * @param s the string
   * @return the string quoted and escaped as Python would print it
   */
  static String pyRepr(final String s) {
    char q = '\'';
    if (s.indexOf('\'') >= 0 && s.indexOf('"') < 0) {
      q = '"';
    }
    StringBuilder sb = new StringBuilder().append(q);
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      switch (c) {
        case '\\':
          sb.append("\\\\");
          break;
        case '\n':
          sb.append("\\n");
          break;
        case '\t':
          sb.append("\\t");
          break;
        case '\r':
          sb.append("\\r");
          break;
        default:
          if (c == q) {
            sb.append('\\');
          }
          sb.append(c);
      }
    }
    return sb.append(q).toString();
  }
}

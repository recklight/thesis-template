%% thesis_report.cls -- document class for a master's thesis.
%%
%% Copyright (c) 2026 RL.  Released under the MIT licence; see
%% https://github.com/recklight/thesis-template
%%
%% Built on the standard `report' class.  \@chapter,
%% \@makechapterhead and the \section/\subsection spacing follow
%% report.cls; the headings are set in smaller sizes and a chapter heading
%% has less space around it.  Apart from that it changes four things, so
%% whatever you know about `report' still holds:
%%
%%   1. Chapter and section headings are centred.
%%   2. Chapter numbers pass through \countermapping, a hook that is the
%%      identity by default and is redefined to produce Chinese numerals
%%      when a CJK numbering style is selected.
%%   3. Table-of-contents chapter lines get leader dots, with the width of
%%      the number column controlled by \tocChNumberWidth.
%%   4. \prechaptername / \postchaptername wrap the chapter number, so
%%      "Chapter 1" and "第一章" come from the same code with different
%%      settings.
%%
%% Class options are passed straight through to `report'.  Two extra
%% options are recognised:
%%
%%   flushchapters   chapter headings flush left instead of centred
%%   flushsections   section headings flush left instead of centred

\NeedsTeXFormat{LaTeX2e}[2005/12/01]
\ProvidesClass{thesis_report}[2025/06/01 v1.0 Master's thesis class]

\newif\ifthesis@centerchapters \thesis@centerchapterstrue
\newif\ifthesis@centersections \thesis@centersectionstrue
\DeclareOption{flushchapters}{\thesis@centerchaptersfalse}
\DeclareOption{flushsections}{\thesis@centersectionsfalse}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{report}}
\ProcessOptions\relax
\LoadClass{report}

\RequirePackage{setspace}

%% ------------------------------------------------------------------
%% Hooks for chapter numbering
%% ------------------------------------------------------------------
%% \countermapping is the identity until a numbering style redefines it.
%% As a hook it keeps Chinese numerals out of the class altogether; the
%% numbering styles in config/chapter_numbering.tex redefine it.
\newcommand*{\countermapping}[1]{#1}

%% Text placed before and after the chapter number in the heading.
\newcommand*{\prechaptername}{\@chapapp\space}
\newcommand*{\postchaptername}{}

%% The same, for the table of contents, where the available width is
%% different and a trailing separator is usually wanted.
\newcommand*{\tocprechaptername}{}
\newcommand*{\tocpostchaptername}{}

%% Width of the number column in the table of contents.
%%
%% Rule of thumb: (number of characters in the widest rendered number,
%% including \tocprechaptername and \tocpostchaptername) + 1, measured in
%% em, and never below 1.4em.  The three shipped styles use 1.4em for
%% "1.", 2em for "一、" and 3em for "第一章、".
\newlength{\tocChNumberWidth}
\setlength{\tocChNumberWidth}{1.4em}

%% Vertical spacing around a chapter heading; exposed so a department's
%% formatting rules can be met without editing this file.
\newlength{\thesisChapterHeadSkip}
\newlength{\thesisChapterHeadSep}
\setlength{\thesisChapterHeadSkip}{20\p@}
\setlength{\thesisChapterHeadSep}{20\p@}

%% ------------------------------------------------------------------
%% Chapter headings
%% ------------------------------------------------------------------
\newcommand{\thesis@headalign}{%
  \ifthesis@centerchapters\centering\fi}

\def\@makechapterhead#1{%
  \vspace*{\thesisChapterHeadSkip}%
  {\parindent\z@
   \thesis@headalign
   \normalfont
   \ifnum \c@secnumdepth >\m@ne
     \large\bfseries
     \prechaptername\countermapping{\thechapter}\postchaptername
     \par\nobreak
     \vskip 10\p@
   \fi
   \interlinepenalty\@M
   \Large\bfseries #1\par\nobreak
   \vskip \thesisChapterHeadSep
  }}

\def\@makeschapterhead#1{%
  \vspace*{\thesisChapterHeadSkip}%
  {\parindent\z@
   \thesis@headalign
   \normalfont
   \interlinepenalty\@M
   \Large\bfseries #1\par\nobreak
   \vskip \thesisChapterHeadSep
  }}

%% \@chapter differs from the one in report.cls only in the argument
%% handed to \numberline: the raw \thechapter is replaced by the mapped
%% number wrapped in the table-of-contents affixes.
\def\@chapter[#1]#2{%
  \ifnum \c@secnumdepth >\m@ne
    \refstepcounter{chapter}%
    \typeout{\@chapapp\space\thechapter.}%
    \addcontentsline{toc}{chapter}{%
      \protect\numberline{%
        \tocprechaptername\countermapping{\thechapter}\tocpostchaptername}#1}%
  \else
    \addcontentsline{toc}{chapter}{#1}%
  \fi
  \chaptermark{#1}%
  \addtocontents{lof}{\protect\addvspace{10\p@}}%
  \addtocontents{lot}{\protect\addvspace{10\p@}}%
  \if@twocolumn
    \@topnewpage[\@makechapterhead{#2}]%
  \else
    \@makechapterhead{#2}%
    \@afterheading
  \fi}

%% ------------------------------------------------------------------
%% Table of contents
%% ------------------------------------------------------------------
%% report.cls sets chapter lines in bold with no leader dots.  A thesis
%% table of contents is easier to read with dots, and the number column
%% needs a different width for each numbering style, hence
%% \tocChNumberWidth.
\renewcommand*{\l@chapter}[2]{%
  \@dottedtocline{0}{0em}{\tocChNumberWidth}{#1}{#2}}

%% ------------------------------------------------------------------
%% Section headings
%% ------------------------------------------------------------------
\newcommand{\thesis@secalign}{%
  \ifthesis@centersections\centering\fi}

\renewcommand{\section}{%
  \@startsection{section}{1}{\z@}%
    {-3.5ex \@plus -1ex \@minus -.2ex}%
    {2.3ex \@plus .2ex}%
    {\thesis@secalign\normalfont\large\bfseries}}

\renewcommand{\subsection}{%
  \@startsection{subsection}{2}{\z@}%
    {-3.25ex \@plus -1ex \@minus -.2ex}%
    {1.5ex \@plus .2ex}%
    {\normalfont\normalsize\bfseries}}

\endinput

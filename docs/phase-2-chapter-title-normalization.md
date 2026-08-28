# Chapter Title Normalization

LibriVox MP3 files may already contain useful chapter titles such as:

```text
01 - Chapter I
12 - Chapter  XII
```

Mnemosyne already stores ordering independently through canonical filenames and
ID3 `TRCK=N/total`, so retaining a second numeric prefix in `TIT2` is redundant.

For multi-file audiobook tagging, Mnemosyne now:

- strips only a leading numeric prefix plus separator
- collapses repeated whitespace
- preserves the human chapter title itself
- keeps canonical filename numbering unchanged
- keeps ID3 track numbering unchanged

Examples:

```text
01 - Chapter I    -> Chapter I
12 - Chapter  XII -> Chapter XII
The Cylinder      -> The Cylinder
```

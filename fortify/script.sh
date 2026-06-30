#!/usr/bin/env bash

total_start=$(date +%s)

for i in {100,200,700,800,1100,1200,1600,1700}
do
    design="aes${i}_recon"
    outdir="results/aes/${design}"
    log_file="log_aes${i}.txt"

    mkdir -p "$outdir"

    start_time=$(date +%s)
    start_human=$(date '+%Y-%m-%d %H:%M:%S')

    echo "============================================================"
    echo "Starting design: ${design}"
    echo "Start time: ${start_human}"
    echo "Log file: ${log_file}"
    echo "============================================================"

    {
        echo "============================================================"
        echo "Starting design: ${design}"
        echo "Start time: ${start_human}"
        echo "============================================================"
    } > "$log_file"

    python3 run_react_article.py \
        Benchmarks/AES${i}.v \
        top top top top.key 128 \
        ${design} \
        --reconvergence-aware \
        -r aes >> "$log_file" 2>&1

    status=$?

    end_time=$(date +%s)
    end_human=$(date '+%Y-%m-%d %H:%M:%S')
    elapsed=$((end_time - start_time))

    elapsed_fmt=$(printf '%02d:%02d:%02d' \
        $((elapsed / 3600)) \
        $(((elapsed % 3600) / 60)) \
        $((elapsed % 60)))

    {
        echo ""
        echo "============================================================"
        echo "Finished design: ${design}"
        echo "End time: ${end_human}"
        echo "Time taken: ${elapsed_fmt} HH:MM:SS"
        echo "Exit status: ${status}"
        echo "============================================================"
    } | tee -a "$log_file"

    # Move all generated .txt files into this design's result directory.
    # This includes log_aes${i}.txt, truthTableMap.txt, parents.txt, etc.
    for f in ./*.txt
    do
        [ -e "$f" ] || continue
        mv "$f" "$outdir/"
    done

    if [ "$status" -ne 0 ]; then
        echo "WARNING: ${design} failed with exit status ${status}"
        echo "Check: ${outdir}/${log_file}"
    else
        echo "Completed ${design} successfully in ${elapsed_fmt}"
    fi

    echo ""
done

total_end=$(date +%s)
total_elapsed=$((total_end - total_start))

total_elapsed_fmt=$(printf '%02d:%02d:%02d' \
    $((total_elapsed / 3600)) \
    $(((total_elapsed % 3600) / 60)) \
    $((total_elapsed % 60)))

echo "============================================================"
echo "All designs completed"
echo "Total time taken: ${total_elapsed_fmt} HH:MM:SS"
echo "============================================================"
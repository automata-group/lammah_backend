package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestProbeDurationAndConvert(t *testing.T) {
	// Locate sample ogg file in root or parent
	sampleFile := filepath.Join("..", "WhatsApp Ptt 2026-11-04 at 11.20.01 PM.ogg")
	if _, err := os.Stat(sampleFile); os.IsNotExist(err) {
		sampleFile = "WhatsApp Ptt 2026-11-04 at 11.20.01 PM.ogg"
	}
	if _, err := os.Stat(sampleFile); os.IsNotExist(err) {
		t.Skip("Sample audio file not found, skipping live media test")
		return
	}

	// 1. Test probeDuration
	dur, err := probeDuration(sampleFile)
	if err != nil {
		t.Fatalf("probeDuration failed: %v", err)
	}
	if dur <= 0 {
		t.Fatalf("expected positive duration, got %f", dur)
	}
	t.Logf("✅ probeDuration success: %.2f seconds", dur)

	// 2. Test convertAudioToStandardizedMonoMP3
	tmpDir, err := os.MkdirTemp("", "media_test_*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(tmpDir)

	dstMP3 := filepath.Join(tmpDir, "standard.mp3")
	if err := convertAudioToStandardizedMonoMP3(sampleFile, dstMP3); err != nil {
		t.Fatalf("convertAudioToStandardizedMonoMP3 failed: %v", err)
	}

	info, err := os.Stat(dstMP3)
	if err != nil || info.Size() == 0 {
		t.Fatalf("converted mp3 missing or empty: %v", err)
	}
	t.Logf("✅ convertAudioToStandardizedMonoMP3 success: size=%d bytes", info.Size())

	// 3. Test mergeMultipleAudios
	ctx := context.Background()
	mergedMP3 := filepath.Join(tmpDir, "merged.mp3")
	mergedDur, err := mergeMultipleAudios(ctx, nil, "test_job_1", []string{sampleFile, sampleFile}, mergedMP3, tmpDir)
	if err != nil {
		t.Fatalf("mergeMultipleAudios failed: %v", err)
	}
	if mergedDur <= dur {
		t.Fatalf("expected merged duration > single duration (%.2f > %.2f)", mergedDur, dur)
	}
	t.Logf("✅ mergeMultipleAudios success: mergedDur=%.2f seconds", mergedDur)

	// 4. Test chunkAudio
	chunkDir := filepath.Join(tmpDir, "chunks")
	chunks, err := chunkAudio(ctx, nil, "test_job_1", mergedMP3, chunkDir, 10)
	if err != nil {
		t.Fatalf("chunkAudio failed: %v", err)
	}
	if len(chunks) == 0 {
		t.Fatalf("expected at least 1 chunk, got 0")
	}
	t.Logf("✅ chunkAudio success: generated %d chunks", len(chunks))
}

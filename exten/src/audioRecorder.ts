import * as recorder from 'node-record-lpcm16';
import Speaker from 'speaker';

// Debug flag - set to true to enable verbose logging
const DEBUG_AUDIO = true;

function debugLog(...args: any[]): void {
    if (DEBUG_AUDIO) {
        console.log('[AudioRecorder]', new Date().toISOString(), ...args);
    }
}

/**
 * AudioRecorder - Handles microphone recording and AI audio playback.
 * 
 * Uses native 'speaker' package for direct PCM audio playback (no external commands).
 * 
 * PLAYBACK APPROACH (STREAMING - LOW LATENCY):
 * 1. Create Speaker instance on first chunk arrival
 * 2. Stream chunks directly to speaker as they arrive (NO BUFFERING)
 * 3. On turn_complete or interrupt, end the speaker stream
 * 4. This ensures first audio plays immediately, not after full response
 */
export class AudioRecorder {
    // Recording state
    private recording: any = null;
    private muted = false;
    private isRecordingActive = false;
    private shouldStopSending = false;
    private vadEnabled = false;
    private voiceStartTime: number = 0;
    
    // VAD configuration
    private readonly SILENCE_THRESHOLD = 0.005;
    private readonly MIN_VOICE_DURATION = 100;
    
    // Streaming playback state (LOW LATENCY)
    private speaker: Speaker | null = null;
    private isPlaying = false;
    private chunkCount = 0;
    private totalBytesPlayed = 0;
    private firstChunkTime: number = 0;  // For latency measurement

    async start(onAudioData: (data: Uint8Array) => void): Promise<void> {
        try {
            this.isRecordingActive = true;
            this.shouldStopSending = false;
            
            debugLog('Starting audio recording');
            
            this.recording = recorder.record({
                sampleRate: 16000,
                channels: 1,
                threshold: 0,
                recorder: 'sox',
                endOnSilence: false
            });

            const recordingStream = this.recording.stream();

            recordingStream.on('data', (data: Buffer) => {
                if (!this.muted && this.isRecordingActive) {
                    if (this.shouldStopSending) {
                        return;
                    }
                    
                    if (!this.vadEnabled) {
                        onAudioData(new Uint8Array(data));
                        return;
                    }
                    
                    const hasVoice = this.detectVoiceActivity(data);
                    if (hasVoice) {
                        debugLog('Voice detected - sending audio data');
                        onAudioData(new Uint8Array(data));
                    }
                }
            });

            recordingStream.on('error', (err: Error) => {
                console.error('[AudioRecorder] Recording error:', err);
            });

            debugLog('Audio recording started');
        } catch (error) {
            console.error('[AudioRecorder] Failed to start audio recording:', error);
            throw new Error(`Failed to access microphone. Install SoX: brew install sox`);
        }
    }

    /**
     * Play audio chunk IMMEDIATELY via streaming speaker.
     * Creates speaker on first chunk, streams subsequent chunks directly.
     * This ensures low-latency playback - audio plays as it arrives!
     */
    async playAudio(audioData: Uint8Array, sessionId?: string): Promise<void> {
        try {
            if (this.muted) {
                return;
            }

            // First chunk - create speaker and start streaming
            if (!this.speaker) {
                this.firstChunkTime = Date.now();
                debugLog(`Starting streaming playback - first chunk received`);
                
                try {
                    // Create speaker with Gemini audio format (24kHz, 16-bit, mono)
                    this.speaker = new Speaker({
                        channels: 1,           // mono
                        bitDepth: 16,          // 16-bit
                        sampleRate: 24000,     // 24kHz (Gemini output)
                        highWaterMark: 4096    // Smaller buffer for lower latency
                    });

                    this.speaker.on('error', (error: Error) => {
                        console.error('[AudioRecorder] Speaker error:', error);
                        this.cleanupSpeaker();
                    });

                    this.speaker.on('close', () => {
                        debugLog(`Speaker closed - played ${this.chunkCount} chunks, ${this.totalBytesPlayed} bytes`);
                        this.cleanupSpeaker();
                    });

                    this.isPlaying = true;
                    this.chunkCount = 0;
                    this.totalBytesPlayed = 0;
                    
                } catch (error) {
                    console.error('[AudioRecorder] Error creating speaker:', error);
                    return;
                }
            }

            // Stream chunk directly to speaker (NO BUFFERING!)
            if (this.speaker && !this.speaker.destroyed) {
                const buffer = Buffer.from(audioData);
                
                // Write with error handling
                const writeResult = this.speaker.write(buffer);
                if (!writeResult) {
                    debugLog('Speaker write returned false - backpressure detected, but continuing');
                }
                
                this.chunkCount++;
                this.totalBytesPlayed += buffer.length;
                
                if (this.chunkCount === 1) {
                    debugLog(`First audio chunk playing! Latency: ${Date.now() - this.firstChunkTime}ms`);
                }
                
                if (this.chunkCount % 20 === 0) {
                    debugLog(`Streaming: ${this.chunkCount} chunks, ${this.totalBytesPlayed} bytes`);
                }
            } else if (!this.speaker) {
                debugLog('Warning: playAudio called but speaker not ready');
            }
        } catch (error) {
            console.error('[AudioRecorder] Error in playAudio:', error);
        }
    }

    /**
     * Signal that AI response is complete - finalize streaming playback.
     */
    async endCurrentSession(): Promise<void> {
        if (this.speaker && !this.speaker.destroyed) {
            debugLog(`Ending playback session - ${this.chunkCount} chunks streamed`);
            this.speaker.end();  // Signal end of stream
        }
        // Speaker will clean itself up via 'close' event
    }

    /**
     * Immediately stop playback (user interrupted).
     */
    async cancelCurrentSession(): Promise<void> {
        this.interruptPlayback();
    }

    private cleanupSpeaker(): void {
        this.speaker = null;
        this.isPlaying = false;
        this.chunkCount = 0;
        this.totalBytesPlayed = 0;
    }

    interruptPlayback(): void {
        debugLog('Interrupting AI playback');
        if (this.speaker) {
            try {
                this.speaker.destroy();
            } catch (error) {
                // Ignore destroy errors
            }
        }
        this.cleanupSpeaker();
    }

    stop(): void {
        debugLog('Stopping AudioRecorder completely');
        if (this.recording) {
            this.recording.stop();
            this.recording = null;
        }
        this.isRecordingActive = false;
        this.interruptPlayback();
        debugLog('Audio recording stopped');
    }

    toggleMute(): void {
        this.muted = !this.muted;
        debugLog(`Mute toggled: ${this.muted}`);
    }

    isMuted(): boolean {
        return this.muted;
    }

    clearPlaybackQueue(): void {
        // With streaming, there's no queue - just interrupt current playback
        this.interruptPlayback();
    }

    private detectVoiceActivity(audioData: Buffer): boolean {
        const samples = new Int16Array(audioData.buffer, audioData.byteOffset, audioData.byteLength / 2);
        const energy = this.calculateRMSEnergy(Array.from(samples));
        const hasVoice = energy > this.SILENCE_THRESHOLD;
        
        if (hasVoice) {
            if (this.voiceStartTime === 0) {
                this.voiceStartTime = Date.now();
                debugLog(`Voice activity started, energy: ${energy.toFixed(4)}`);
            }
            const voiceDuration = Date.now() - this.voiceStartTime;
            if (voiceDuration > this.MIN_VOICE_DURATION) {
                return true;
            }
        } else {
            if (this.voiceStartTime > 0) {
                debugLog(`Voice activity stopped, energy: ${energy.toFixed(4)}`);
            }
            this.voiceStartTime = 0;
        }
        
        return false;
    }
    
    private calculateRMSEnergy(samples: number[]): number {
        if (samples.length === 0) return 0;
        
        let sum = 0;
        for (const sample of samples) {
            sum += (sample / 32768) * (sample / 32768);
        }
        return Math.sqrt(sum / samples.length);
    }

    pauseSending(): void {
        this.shouldStopSending = true;
        debugLog('Audio sending paused');
    }
    
    resumeSending(): void {
        this.shouldStopSending = false;
        this.voiceStartTime = 0;
        debugLog('Audio sending resumed');
    }
    
    isCurrentlySending(): boolean {
        return this.isRecordingActive && !this.shouldStopSending;
    }

    enablePassthrough(): void {
        this.vadEnabled = false;
        debugLog('VAD disabled');
    }

    enableVAD(): void {
        this.vadEnabled = true;
        debugLog('VAD enabled');
    }
    
    isPlaybackActive(): boolean {
        return this.isPlaying;
    }

    getStatus(): string {
        return `Recording: ${this.isRecordingActive}, Sending: ${!this.shouldStopSending}, Muted: ${this.muted}, Playing: ${this.isPlaying}, Chunks: ${this.chunkCount}`;
    }
}
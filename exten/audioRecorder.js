"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.AudioRecorder = void 0;
class AudioRecorder {
    constructor() {
        this.mediaRecorder = null;
        this.audioContext = null;
        this.audioQueue = [];
        this.isPlaying = false;
        this.muted = false;
        this.stream = null;
    }
    async start(onAudioData) {
        try {
            // Request microphone access
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: 16000,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            // Create MediaRecorder with 16kHz PCM
            this.mediaRecorder = new MediaRecorder(this.stream, {
                mimeType: 'audio/webm;codecs=opus',
                audioBitsPerSecond: 16000
            });
            this.mediaRecorder.ondataavailable = async (event) => {
                if (event.data.size > 0 && !this.muted) {
                    // Convert to required format (16kHz, 16-bit PCM)
                    const audioData = await this.convertAudioFormat(event.data);
                    onAudioData(audioData);
                }
            };
            // Capture audio in small chunks (100ms)
            this.mediaRecorder.start(100);
            console.log('Audio recording started');
        }
        catch (error) {
            console.error('Failed to start audio recording:', error);
            throw new Error('Microphone access denied or not available');
        }
    }
    async convertAudioFormat(blob) {
        // Convert WebM/Opus to 16kHz 16-bit PCM
        const arrayBuffer = await blob.arrayBuffer();
        if (!this.audioContext) {
            this.audioContext = new AudioContext({ sampleRate: 16000 });
        }
        const audioBuffer = await this.audioContext.decodeAudioData(arrayBuffer);
        const channelData = audioBuffer.getChannelData(0);
        // Convert float32 to int16
        const int16Array = new Int16Array(channelData.length);
        for (let i = 0; i < channelData.length; i++) {
            const s = Math.max(-1, Math.min(1, channelData[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return new Uint8Array(int16Array.buffer);
    }
    async playAudio(audioData) {
        if (this.muted)
            return;
        this.audioQueue.push(audioData);
        if (!this.isPlaying) {
            this.playNextInQueue();
        }
    }
    async playNextInQueue() {
        if (this.audioQueue.length === 0) {
            this.isPlaying = false;
            return;
        }
        this.isPlaying = true;
        const audioData = this.audioQueue.shift();
        try {
            if (!this.audioContext) {
                this.audioContext = new AudioContext({ sampleRate: 16000 });
            }
            // Convert int16 PCM to AudioBuffer
            const int16Array = new Int16Array(audioData.buffer);
            const float32Array = new Float32Array(int16Array.length);
            for (let i = 0; i < int16Array.length; i++) {
                float32Array[i] = int16Array[i] / (int16Array[i] < 0 ? 0x8000 : 0x7FFF);
            }
            const audioBuffer = this.audioContext.createBuffer(1, float32Array.length, 16000);
            audioBuffer.getChannelData(0).set(float32Array);
            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);
            source.onended = () => {
                this.playNextInQueue();
            };
            source.start();
        }
        catch (error) {
            console.error('Error playing audio:', error);
            this.playNextInQueue(); // Continue with next chunk
        }
    }
    stop() {
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
        this.audioQueue = [];
        this.isPlaying = false;
        console.log('Audio recording stopped');
    }
    toggleMute() {
        this.muted = !this.muted;
    }
    isMuted() {
        return this.muted;
    }
    clearPlaybackQueue() {
        this.audioQueue = [];
    }
}
exports.AudioRecorder = AudioRecorder;
//# sourceMappingURL=audioRecorder.js.map
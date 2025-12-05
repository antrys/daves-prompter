"""
Fragment-based word matching for teleprompter.

Splits script into natural fragments (using punctuation as delimiters),
then fuzzy matches entire fragments against spoken text.

This matches how people actually read - in phrases and clauses, not individual words.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from rapidfuzz import fuzz


@dataclass
class Fragment:
    """A fragment of the script (phrase/clause)."""
    text: str               # Original text
    normalized: str         # Lowercase, cleaned for matching
    index: int              # Fragment index
    word_start: int         # Starting word index in full script
    word_end: int           # Ending word index in full script


@dataclass
class MatchResult:
    """Result of matching."""
    word_index: int         # Current word position
    fragment_index: int     # Current fragment index
    confidence: float       # Match confidence (0-1)
    matched_words: List[int]


class WordMatcher:
    """
    Fragment-based teleprompter matcher.
    
    Splits script into fragments at punctuation, then fuzzy matches
    entire fragments against spoken text.
    """

    def __init__(self):
        self.fragments: List[Fragment] = []
        self.current_fragment: int = 0
        self.current_position: int = 0  # Word position for display
        self.matched_positions: List[int] = []
        
        # Fragment matching threshold (0-100)
        self.match_threshold = 55
        
        # Maximum words we can jump at once (prevents wild jumps)
        self.max_jump = 25
        
        # How much to weight proximity to current position
        self.proximity_weight = 20
        
    def set_script(self, text: str):
        """
        Parse script into fragments.
        
        Strategy:
        1. Split by paragraphs FIRST (double newlines are HARD boundaries)
        2. Within each paragraph, split by sentences (. ! ?)
        3. Combine only within same sentence if too short
        """
        self.fragments = []
        self.current_fragment = 0
        self.current_position = 0
        self.matched_positions = []
        
        word_position = 0
        fragment_idx = 0
        
        # First, split into paragraphs (hard boundaries - never combine across these)
        paragraphs = re.split(r'\n\s*\n', text)
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Within paragraph, split by sentence-ending punctuation
            # Keep the punctuation with the sentence
            sentences = re.split(r'(?<=[.!?])\s+', para)
            
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                
                # Within sentence, split by clause markers (commas, semicolons, etc)
                clauses = re.split(r'[,;:]\s+|\s*[-–—]\s+', sentence)
                
                pending_text = ""
                pending_word_start = word_position
                
                for clause in clauses:
                    clause = clause.strip()
                    if not clause:
                        continue
                    
                    words = re.findall(r"[\w']+", clause)
                    if not words:
                        continue
                    
                    # Accumulate short clauses within the same sentence
                    if pending_text:
                        pending_text += ", " + clause
                    else:
                        pending_text = clause
                        pending_word_start = word_position
                    
                    word_position += len(words)
                    
                    # Create fragment if we have at least 3 words
                    pending_words = re.findall(r"[\w']+", pending_text)
                    if len(pending_words) >= 3:
                        self.fragments.append(Fragment(
                            text=pending_text,
                            normalized=self._normalize(pending_text),
                            index=fragment_idx,
                            word_start=pending_word_start,
                            word_end=word_position - 1
                        ))
                        fragment_idx += 1
                        pending_text = ""
                
                # Flush any remaining text from this sentence (even if short)
                if pending_text:
                    self.fragments.append(Fragment(
                        text=pending_text,
                        normalized=self._normalize(pending_text),
                        index=fragment_idx,
                        word_start=pending_word_start,
                        word_end=word_position - 1
                    ))
                    fragment_idx += 1
                    pending_text = ""
        
        # Debug output - show ALL fragments so we can see what's happening
        print(f"[Matcher] Parsed {len(self.fragments)} fragments from script")
        print(f"[Matcher] === ALL FRAGMENTS ===")
        for i, f in enumerate(self.fragments):
            # Truncate long text for readability
            display_text = f.text[:60] + "..." if len(f.text) > 60 else f.text
            print(f"  [{i}] words {f.word_start:3d}-{f.word_end:3d}: '{display_text}'")
        print(f"[Matcher] === END FRAGMENTS ===")
    
    def _normalize(self, text: str) -> str:
        """Normalize text for matching."""
        # Lowercase, remove punctuation, normalize whitespace
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    
    def get_word_count(self) -> int:
        """Get total word count."""
        if not self.fragments:
            return 0
        return self.fragments[-1].word_end + 1
    
    def get_word_at(self, index: int):
        """Get word at index (for compatibility)."""
        # Find which fragment contains this word
        for f in self.fragments:
            if f.word_start <= index <= f.word_end:
                # Extract the word from the fragment
                words = re.findall(r"[\w']+", f.text)
                word_offset = index - f.word_start
                if 0 <= word_offset < len(words):
                    return type('Word', (), {'text': words[word_offset], 'index': index})()
        return None
    
    def get_current_word(self):
        return self.get_word_at(self.current_position)
    
    def reset(self):
        """Reset to beginning."""
        self.current_fragment = 0
        self.current_position = 0
        self.matched_positions = []
    
    def _score_fragment(self, spoken_normalized: str, fragment: Fragment) -> float:
        """
        Score how well spoken text matches a fragment.
        
        Uses fuzzy matching but requires good overlap to prevent
        "smaller" matching "small" in a distant fragment.
        """
        if not spoken_normalized or not fragment.normalized:
            return 0
        
        # Use token_set_ratio - handles word order and extra words well
        # but requires actual word matches, not just similar characters
        score = fuzz.token_set_ratio(spoken_normalized, fragment.normalized)
        
        # Penalize if lengths are very different (prevents partial word matches)
        len_ratio = min(len(spoken_normalized), len(fragment.normalized)) / max(len(spoken_normalized), len(fragment.normalized))
        if len_ratio < 0.3:
            score *= 0.7  # Significant penalty for very different lengths
        
        return score
    
    def _find_best_fragment(self, spoken_text: str, verbose: bool = False) -> tuple:
        """
        Find which fragment best matches the spoken text.
        
        Checks ALL fragments but weights by proximity to current position.
        This allows re-reading and going back.
        
        Returns: (best_fragment_index, score, is_confident)
        """
        if not spoken_text or not self.fragments:
            return (self.current_fragment, 0, False)
        
        spoken_normalized = self._normalize(spoken_text)
        
        # Skip if too short to match reliably (about 2 words minimum)
        if len(spoken_normalized) < 6:
            return (self.current_fragment, 0, False)
        
        best_idx = self.current_fragment
        best_score = 0
        all_scores = []  # For logging
        
        # Calculate the word position limit (can't jump too far)
        current_word_pos = self.fragments[self.current_fragment].word_end if self.current_fragment < len(self.fragments) else 0
        max_word_pos = current_word_pos + self.max_jump
        
        for fragment in self.fragments:
            # Skip fragments that are too far ahead
            if fragment.word_start > max_word_pos:
                continue
            # Base score from fuzzy matching
            base_score = self._score_fragment(spoken_normalized, fragment)
            
            # Proximity bonus/penalty - STRONGLY favor moving forward
            distance = abs(fragment.index - self.current_fragment)
            
            if fragment.index == self.current_fragment:
                # CURRENT fragment: bonus to prevent premature jumps
                proximity_bonus = 20
            elif fragment.index == self.current_fragment + 1:
                # NEXT fragment: if it matches WELL (>75), give it priority to advance
                # This lets us move forward when user starts reading the next sentence
                if base_score >= 75:
                    proximity_bonus = 25  # Beats current fragment bonus
                else:
                    proximity_bonus = 5   # Low bonus unless it matches well
            elif fragment.index > self.current_fragment:
                # Further ahead: penalty
                proximity_bonus = -distance * 2
            else:
                # Going backward: heavy penalty
                proximity_bonus = -distance * 10
            
            final_score = base_score + proximity_bonus
            
            # Track for logging
            if base_score > 30:  # Only log meaningful scores
                all_scores.append((fragment.index, base_score, final_score, fragment.text[:30]))
            
            if final_score > best_score:
                best_score = final_score
                best_idx = fragment.index
        
        # Is this a confident match?
        raw_best_score = self._score_fragment(spoken_normalized, self.fragments[best_idx])
        is_confident = raw_best_score >= self.match_threshold
        
        # Detailed logging
        if verbose or len(spoken_normalized) > 15:
            print(f"\n[Match] Spoken: '{spoken_text[:50]}...' (normalized: {len(spoken_normalized)} chars)")
            print(f"[Match] Current fragment: {self.current_fragment}")
            print(f"[Match] Top matches:")
            for idx, base, final, text in sorted(all_scores, key=lambda x: -x[2])[:5]:
                marker = " <-- BEST" if idx == best_idx else ""
                print(f"        [{idx}] base={base:.0f} final={final:.0f} '{text}...'{marker}")
            print(f"[Match] Best: frag {best_idx}, raw={raw_best_score:.0f}, threshold={self.match_threshold}, confident={is_confident}")
        
        return (best_idx, best_score, is_confident)
    
    def match_words(self, spoken_words: List[str]) -> MatchResult:
        """
        Match spoken words (as a phrase) to a fragment.
        """
        if not spoken_words or not self.fragments:
            return MatchResult(
                word_index=self.current_position,
                fragment_index=self.current_fragment,
                confidence=0,
                matched_words=[]
            )
        
        # IMPORTANT: Only use the last N words, not the entire accumulated text
        # Vosk accumulates speech, but we only care about recent words
        # to detect which fragment the user is currently reading
        max_words = 15  # About 1-2 fragments worth
        if len(spoken_words) > max_words:
            spoken_words = spoken_words[-max_words:]
        
        # Join words back into text for fragment matching
        spoken_text = " ".join(spoken_words)
        
        # Enable verbose logging for longer phrases
        verbose = len(spoken_words) >= 4
        best_idx, score, is_confident = self._find_best_fragment(spoken_text, verbose=verbose)
        
        matched_words = []
        
        if is_confident:
            fragment = self.fragments[best_idx]
            old_frag = self.current_fragment
            
            # Only update if we're moving forward
            if best_idx > self.current_fragment:
                self.current_fragment = best_idx
                # Move to the START of the fragment, not the end
                # This way we see the fragment we're currently reading
                self.current_position = fragment.word_start
                
                # Mark the PREVIOUS fragment as matched (grey it out)
                if old_frag < len(self.fragments):
                    prev_frag = self.fragments[old_frag]
                    for i in range(prev_frag.word_start, prev_frag.word_end + 1):
                        if i not in self.matched_positions:
                            self.matched_positions.append(i)
                        matched_words.append(i)
                
                # ALWAYS print when moving - this is important debug info
                print(f"[Match] MOVED: frag {old_frag} -> {best_idx}")
                print(f"        Spoken: '{spoken_text[:50]}...'")
                print(f"        Matched: '{fragment.text[:50]}...'")
                print(f"        Position: {self.current_position}, Greyed: {matched_words}")
            elif verbose:
                print(f"[Match] STAYING at fragment {best_idx} (already there)")
        elif verbose:
            print(f"[Match] NO MOVE: score {score:.0f} not confident enough")
        
        return MatchResult(
            word_index=self.current_position,
            fragment_index=self.current_fragment,
            confidence=score / 100,
            matched_words=matched_words
        )
    
    def match_partial(self, partial_text: str) -> Optional[int]:
        """
        Match partial recognition for real-time preview.
        More aggressive than full matching for responsiveness.
        """
        if not partial_text or not self.fragments:
            return None
        
        # Use the same matching as match_words for consistency
        words = partial_text.split()
        if len(words) >= 2:
            result = self.match_words(words)
            if result.confidence > 0:
                return result.word_index
        
        return None
    
    def get_context(self, before: int = 3, after: int = 10) -> dict:
        """Get words around current position for display."""
        # Find current fragment
        current_frag = None
        if 0 <= self.current_fragment < len(self.fragments):
            current_frag = self.fragments[self.current_fragment]
        
        current = self.current_position
        total_words = self.get_word_count()
        
        before_words = []
        for i in range(max(0, current - before), current):
            word = self.get_word_at(i)
            if word:
                before_words.append({
                    'index': i,
                    'text': word.text,
                    'matched': i in self.matched_positions
                })
        
        current_word = None
        word = self.get_word_at(current)
        if word:
            current_word = {
                'index': current,
                'text': word.text,
                'matched': current in self.matched_positions
            }
        
        after_words = []
        for i in range(current + 1, min(total_words, current + after + 1)):
            word = self.get_word_at(i)
            if word:
                after_words.append({
                    'index': i,
                    'text': word.text,
                    'matched': i in self.matched_positions
                })
        
        return {
            'before': before_words,
            'current': current_word,
            'after': after_words,
            'total_words': total_words,
            'position': current,
            'fragment': self.current_fragment,
            'total_fragments': len(self.fragments)
        }


if __name__ == "__main__":
    matcher = WordMatcher()
    
    script = """
    You go to the store, buy the same cereal you've been buying for years, 
    and when you pour a bowl the next morning, you think, "Huh. That's funny."
    
    The milk looks different somehow. Not bad, just... different. You shrug 
    it off and take a bite. It tastes fine. Normal, even.
    """
    
    matcher.set_script(script)
    print()
    
    # Simulate reading fragments
    tests = [
        "you go to the store",
        "buy the same cereal",
        "you've been buying for years",
        "and when you pour a bowl",  
        "the next morning",
        "you think huh that's funny",
        "the milk looks different",
    ]
    
    print("Testing fragment matching:\n")
    for spoken in tests:
        # Simulate Vosk output (split into words)
        words = spoken.split()
        result = matcher.match_words(words)
        
        current_frag = matcher.fragments[matcher.current_fragment] if matcher.fragments else None
        print(f"Spoke: '{spoken}'")
        print(f"  -> Fragment {result.fragment_index}: '{current_frag.text[:40] if current_frag else '?'}...'")
        print(f"     Confidence: {result.confidence:.2f}, Word position: {result.word_index}")
        print()

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
        
        Splits on: . , ; : ! ? - and newlines
        Keeps quotes together when possible.
        """
        self.fragments = []
        self.current_fragment = 0
        self.current_position = 0
        self.matched_positions = []
        
        # Split on punctuation but keep meaningful chunks
        # This regex splits on punctuation followed by space, or newlines
        split_pattern = r'[.!?]+\s*|[,;:]\s+|[\n\r]+|\s*[-–—]\s+'
        
        raw_fragments = re.split(split_pattern, text)
        
        word_position = 0
        fragment_idx = 0
        
        pending_text = ""
        pending_word_start = 0
        
        for raw in raw_fragments:
            fragment_text = raw.strip()
            if not fragment_text:
                continue
            
            words = re.findall(r"[\w']+", fragment_text)
            if not words:
                continue
            
            # Accumulate short fragments until we have at least 4 words
            if pending_text:
                pending_text += " " + fragment_text
            else:
                pending_text = fragment_text
                pending_word_start = word_position
            
            word_position += len(words)
            
            # Only create a fragment if we have enough words (at least 4)
            pending_words = re.findall(r"[\w']+", pending_text)
            if len(pending_words) >= 4:
                self.fragments.append(Fragment(
                    text=pending_text,
                    normalized=self._normalize(pending_text),
                    index=fragment_idx,
                    word_start=pending_word_start,
                    word_end=word_position - 1
                ))
                fragment_idx += 1
                pending_text = ""
        
        # Don't forget any remaining text
        if pending_text:
            self.fragments.append(Fragment(
                text=pending_text,
                normalized=self._normalize(pending_text),
                index=fragment_idx,
                word_start=pending_word_start,
                word_end=word_position - 1
            ))
        
        # Debug output
        print(f"[Matcher] Parsed {len(self.fragments)} fragments from script")
        for i, f in enumerate(self.fragments[:5]):  # Show first 5
            print(f"  [{i}] '{f.text[:50]}...' (words {f.word_start}-{f.word_end})")
        if len(self.fragments) > 5:
            print(f"  ... and {len(self.fragments) - 5} more")
    
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
            
            if fragment.index > self.current_fragment:
                # Forward: small penalty for distance, but fragments ahead are OK
                proximity_bonus = max(0, self.proximity_weight - distance)
                # BIG bonus for being the next fragment (natural reading progression)
                if fragment.index == self.current_fragment + 1:
                    proximity_bonus += 20
                elif fragment.index == self.current_fragment + 2:
                    proximity_bonus += 10
            elif fragment.index == self.current_fragment:
                # Current fragment: no bonus (we want to move forward)
                proximity_bonus = 0
            else:
                # Going backward: heavy penalty
                proximity_bonus = -distance * 5
            
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
            
            # Only update if we're moving forward OR it's a different fragment
            # This prevents getting stuck on the same fragment
            if best_idx > self.current_fragment or best_idx != old_frag:
                self.current_fragment = best_idx
                self.current_position = fragment.word_end
                
                # Mark words as matched
                for i in range(fragment.word_start, fragment.word_end + 1):
                    if i not in self.matched_positions:
                        self.matched_positions.append(i)
                    matched_words.append(i)
                
                if verbose:
                    print(f"[Match] MOVED: fragment {old_frag} -> {best_idx}, word position -> {self.current_position}")
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

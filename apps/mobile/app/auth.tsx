import { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { Colors, Radius, Spacing, Typography } from '../constants/theme';
import { useAuth } from '../contexts/AuthContext';
import { ApiError } from '../services/api';

type AuthMode = 'login' | 'register';

export default function AuthScreen(): React.JSX.Element {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const submit = async (): Promise<void> => {
    const validationError = validateAuthInput(mode, username, email, password);
    if (validationError !== null) {
      setError(validationError);
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      if (mode === 'login') {
        await login({ username: username.trim(), password });
      } else {
        await register({ username: username.trim(), email: email.trim(), password });
      }
    } catch (caughtError: unknown) {
      setError(authErrorMessage(caughtError));
    } finally {
      setIsSubmitting(false);
    }
  };

  const switchMode = (): void => {
    setMode((currentMode) => (currentMode === 'login' ? 'register' : 'login'));
    setError(null);
  };

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.heading}>
          <Text style={styles.eyebrow}>KLAUDIA</Text>
          <Text style={styles.title}>
            {mode === 'login' ? 'Sign in to your ledger' : 'Create an account'}
          </Text>
          <Text style={styles.subtitle}>
            Your financial data comes from the Klaudia ledger linked to this account.
          </Text>
        </View>

        <View style={styles.form}>
          <TextInput
            style={styles.input}
            placeholder="Username"
            placeholderTextColor={Colors.textSecondary}
            autoCapitalize="none"
            autoCorrect={false}
            value={username}
            onChangeText={setUsername}
            editable={!isSubmitting}
          />
          {mode === 'register' && (
            <TextInput
              style={styles.input}
              placeholder="Email"
              placeholderTextColor={Colors.textSecondary}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              value={email}
              onChangeText={setEmail}
              editable={!isSubmitting}
            />
          )}
          <TextInput
            style={styles.input}
            placeholder="Password"
            placeholderTextColor={Colors.textSecondary}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            value={password}
            onChangeText={setPassword}
            editable={!isSubmitting}
            onSubmitEditing={() => void submit()}
          />

          {error !== null && <Text style={styles.error}>{error}</Text>}

          <Pressable
            style={({ pressed }) => [
              styles.submit,
              pressed && styles.pressed,
              isSubmitting && styles.disabled,
            ]}
            onPress={() => void submit()}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <ActivityIndicator color="#000000" />
            ) : (
              <Text style={styles.submitText}>
                {mode === 'login' ? 'Sign In' : 'Register'}
              </Text>
            )}
          </Pressable>

          <Pressable onPress={switchMode} disabled={isSubmitting}>
            <Text style={styles.switchText}>
              {mode === 'login'
                ? "Don't have an account? Register"
                : 'Already have an account? Sign in'}
            </Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function validateAuthInput(
  mode: AuthMode,
  username: string,
  email: string,
  password: string,
): string | null {
  if (!/^[a-zA-Z0-9_.-]{3,64}$/.test(username.trim())) {
    return 'Username must be 3-64 characters and contain only letters, numbers, periods, hyphens, or underscores.';
  }
  if (mode === 'register' && !/^\S+@\S+\.\S+$/.test(email.trim())) {
    return 'Enter a valid email address.';
  }
  if (password.length < 8) {
    return 'Password must be at least 8 characters.';
  }
  return null;
}

function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Incorrect username or password.';
    if (error.status === 409) return error.message;
  }
  return 'Unable to sign in. Check your connection and try again.';
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  content: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: Spacing.screenPadding,
    gap: 32,
  },
  heading: {
    gap: 8,
  },
  eyebrow: {
    color: Colors.accent,
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 3,
  },
  title: {
    color: Colors.textPrimary,
    fontSize: 32,
    fontWeight: '700',
  },
  subtitle: {
    ...Typography.body,
    color: Colors.textSecondary,
  },
  form: {
    gap: 12,
  },
  input: {
    minHeight: 52,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.card,
    backgroundColor: Colors.surface,
    color: Colors.textPrimary,
    paddingHorizontal: Spacing.cardPadding,
    fontSize: 16,
  },
  error: {
    color: '#F87171',
    fontSize: 13,
    lineHeight: 19,
  },
  submit: {
    minHeight: 52,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: Radius.card,
    backgroundColor: Colors.accent,
  },
  submitText: {
    color: '#000000',
    fontSize: 15,
    fontWeight: '700',
  },
  switchText: {
    paddingVertical: 8,
    color: Colors.textSecondary,
    textAlign: 'center',
    fontSize: 13,
  },
  pressed: {
    opacity: 0.75,
  },
  disabled: {
    opacity: 0.55,
  },
});

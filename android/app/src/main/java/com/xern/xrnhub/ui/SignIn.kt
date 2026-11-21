package com.xern.xrnhub.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.R
import kotlinx.coroutines.launch

@Composable
fun SignIn() {
    val model = LocalModel.current
    val scope = rememberCoroutineScope()
    var address by rememberSaveable { mutableStateOf(model.address) }
    var user by rememberSaveable { mutableStateOf(model.user) }
    // Held only until it's sent: not saved across a restart, and never stored.
    var secret by remember { mutableStateOf("") }
    var waiting by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<String?>(null) }
    val ready = address.isNotBlank() && user.isNotBlank() && secret.isNotEmpty() && !waiting

    fun send() {
        if (!ready) return
        waiting = true
        problem = null
        scope.launch {
            problem = model.signIn(address, user, secret)
            secret = ""
            waiting = false
        }
    }

    SignInBody(address, { address = it }, user, { user = it }, secret, { secret = it }, waiting, problem, ready, ::send)
}

/** The sign-in page from its fields, so a preview can draw it. */
@Composable
internal fun SignInBody(
    address: String,
    onAddress: (String) -> Unit,
    user: String,
    onUser: (String) -> Unit,
    secret: String,
    onSecret: (String) -> Unit,
    waiting: Boolean,
    problem: String?,
    ready: Boolean,
    send: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Image(painterResource(R.drawable.logo_xrnhub), "xrnhub", Modifier.size(64.dp).clip(RoundedCornerShape(15.dp)))
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            address,
            onAddress,
            Modifier.fillMaxWidth(),
            label = { Text("Server") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false, imeAction = ImeAction.Next),
        )
        OutlinedTextField(
            user,
            onUser,
            Modifier.fillMaxWidth(),
            label = { Text("Username") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, imeAction = ImeAction.Next),
        )
        OutlinedTextField(
            secret,
            onSecret,
            Modifier.fillMaxWidth(),
            label = { Text("Password") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false, imeAction = ImeAction.Go),
            keyboardActions = KeyboardActions(onGo = { send() }),
        )
        problem?.let { Text(it, Modifier.fillMaxWidth(), style = Type.body, color = colors.bad) }
        Button(
            send,
            Modifier.fillMaxWidth().height(48.dp),
            enabled = ready,
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(containerColor = colors.series),
        ) {
            Text(if (waiting) "Signing in" else "Sign in", style = Type.cardTitle)
        }
    }
}
